//! SCIP Engine - Universal SCIP Indexer Runner
//!
//! A CLI tool that runs SCIP indexers on codebases to produce raw .scip
//! protobuf files. No downstream processing — just accurate SCIP generation.

use anyhow::{Context, Result};
use clap::{Parser, Subcommand};
use legend_indexer::{
    config::Config,
    detect::{DetectionReport, Language, LanguageDetector},
    orchestrate::IndexerOrchestrator,
};
use std::fs;
use std::io;
use std::path::PathBuf;
use tracing::{info, Level};
use tracing_subscriber::FmtSubscriber;

#[derive(Parser)]
#[command(name = "scip-engine")]
#[command(author = "Legend Team")]
#[command(version = env!("CARGO_PKG_VERSION"))]
#[command(about = "Universal SCIP indexer runner — produces raw .scip files from any codebase", long_about = None)]
struct Cli {
    #[command(subcommand)]
    command: Option<Commands>,

    /// Path to the codebase to analyze
    #[arg(default_value = ".")]
    path: PathBuf,

    /// Output directory for .scip files (defaults to /tmp/legend-indexer/)
    #[arg(short, long)]
    output: Option<PathBuf>,

    /// Languages to analyze (comma-separated, e.g., "typescript,python")
    #[arg(short, long, value_delimiter = ',')]
    languages: Vec<String>,

    /// Glob patterns to exclude (comma-separated)
    #[arg(short, long, value_delimiter = ',')]
    exclude: Vec<String>,

    /// Path to bundled indexers directory
    #[arg(long)]
    indexers_path: Option<PathBuf>,

    /// Enable verbose output
    #[arg(short, long)]
    verbose: bool,
}

#[derive(Subcommand)]
enum Commands {
    /// Analyze a codebase and produce .scip files
    Analyze {
        /// Path to the codebase
        path: PathBuf,
    },

    /// Detect languages in a codebase
    Detect {
        /// Path to the codebase
        path: PathBuf,
    },

    /// Check which SCIP indexers are available
    CheckIndexers,
}

fn main() -> Result<()> {
    let cli = Cli::parse();

    // Set up logging
    let log_level = if cli.verbose { Level::DEBUG } else { Level::INFO };
    let subscriber = FmtSubscriber::builder()
        .with_max_level(log_level)
        .with_target(false)
        .with_writer(io::stderr)
        .finish();
    tracing::subscriber::set_global_default(subscriber)
        .context("Failed to set up logging")?;

    match cli.command {
        Some(Commands::Detect { path }) => detect_languages(&path),
        Some(Commands::CheckIndexers) => check_indexers(),
        analyze_or_default => {
            let path = match analyze_or_default {
                Some(Commands::Analyze { path }) => path,
                _ => cli.path,
            };
            let mut exclude_patterns = Config::default().exclude_patterns;
            exclude_patterns.extend(cli.exclude);
            let config = Config {
                input_path: path,
                languages: cli.languages,
                exclude_patterns,
                indexers_path: cli.indexers_path,
                verbose: cli.verbose,
                ..Default::default()
            };
            analyze_codebase(config, cli.output)
        }
    }
}

/// Analyze a codebase and produce .scip files
fn analyze_codebase(config: Config, output_dir: Option<PathBuf>) -> Result<()> {
    info!("Analyzing codebase: {:?}", config.input_path);

    // Step 1: Detect languages
    let detector = LanguageDetector::new(&config.exclude_patterns);
    let report = detector.detect(&config.input_path)?;

    // Print coverage summary before running indexers
    print_coverage_summary(&report);

    if report.languages.is_empty() {
        eprintln!("No supported programming languages detected in {:?}", config.input_path);
        return Ok(());
    }

    info!(
        "Detected languages: {:?}",
        report.languages.iter().map(|d| d.language.display_name()).collect::<Vec<_>>()
    );

    // Serialize report JSON before consuming report.languages
    let report_json = serde_json::to_string_pretty(&report)
        .context("Failed to serialize detection report")?;

    // Filter to specified languages if provided
    let languages = if config.languages.is_empty() {
        report.languages
    } else {
        LanguageDetector::filter_languages(report.languages, &config.languages)
    };

    if languages.is_empty() {
        eprintln!("No matching languages found for filter: {:?}", config.languages);
        return Ok(());
    }

    // Step 2: Run indexers (this cleans stale files in .legend-indexer/)
    let orchestrator = IndexerOrchestrator::new(
        config.input_path.clone(),
        config.indexers_path.clone(),
    )?;

    // Write detection report JSON after orchestrator cleans stale files
    let report_path = orchestrator.output_dir().join("detection-report.json");
    fs::write(&report_path, &report_json)
        .with_context(|| format!("Failed to write detection report: {:?}", report_path))?;
    info!("Detection report written to {:?}", report_path);

    let results = orchestrator.run_all(&languages);

    // Collect successful results
    let successful: Vec<_> = results.iter().filter(|r| r.success).collect();

    if successful.is_empty() {
        eprintln!("No indexers completed successfully.");
        eprintln!("\nTo install SCIP indexers:");
        for lang_info in &languages {
            eprintln!("  {}: {}", lang_info.language.display_name(), lang_info.language.install_command());
        }
        return Ok(());
    }

    info!(
        "{} of {} indexers completed successfully",
        successful.len(),
        languages.len()
    );

    // Step 3: Copy .scip files + detection report to output directory (if specified)
    if let Some(ref out_dir) = output_dir {
        fs::create_dir_all(out_dir)
            .with_context(|| format!("Failed to create output directory: {:?}", out_dir))?;

        for result in &successful {
            if result.scip_path.exists() {
                if let Some(name) = result.scip_path.file_name() {
                    let dest = out_dir.join(name);
                    fs::copy(&result.scip_path, &dest)
                        .with_context(|| format!("Failed to copy {:?} to {:?}", result.scip_path, dest))?;
                    info!("Copied {:?} -> {:?}", result.scip_path, dest);
                }
            }
        }

        // Also copy detection report to output dir (before cleanup deletes it)
        if report_path.exists() {
            let dest = out_dir.join("detection-report.json");
            fs::copy(&report_path, &dest)
                .with_context(|| format!("Failed to copy detection report to {:?}", dest))?;
            info!("Copied detection report -> {:?}", dest);
        }
    }

    // Print paths of produced .scip files
    println!("Produced SCIP index files:");
    for result in &successful {
        if result.scip_path.exists() {
            if let Some(name) = result.scip_path.file_name() {
                let display_path = if let Some(ref out_dir) = output_dir {
                    out_dir.join(name)
                } else {
                    result.scip_path.clone()
                };
                println!("  {} -> {}", result.language.display_name(), display_path.display());
            }
        }
    }

    // Cleanup /tmp/legend-indexer/ if we copied files to an output dir
    if output_dir.is_some() {
        if let Err(e) = orchestrator.cleanup() {
            eprintln!("Warning: Failed to cleanup temporary files: {}", e);
        }
    } else {
        info!("SCIP files preserved in {:?}", orchestrator.output_dir());
    }

    Ok(())
}

/// Detect languages in a codebase
fn detect_languages(path: &PathBuf) -> Result<()> {
    let detector = LanguageDetector::new(&[]);
    let report = detector.detect(path)?;

    if report.languages.is_empty() {
        println!("No supported programming languages detected.");
        print_coverage_summary(&report);
        return Ok(());
    }

    println!("Detected languages in {:?}:", path);
    println!();

    for info in &report.languages {
        let bundled = if info.language.is_bundled() {
            " (bundled)"
        } else {
            ""
        };

        println!(
            "  {} - {} files{}",
            info.language.display_name(),
            info.file_count,
            bundled
        );

        if !info.config_files.is_empty() {
            for config in &info.config_files {
                println!(
                    "    - {:?}",
                    config.strip_prefix(path).unwrap_or(config)
                );
            }
        }
    }

    println!();
    print_coverage_summary(&report);

    Ok(())
}

/// Print a human-readable coverage summary to stdout
fn print_coverage_summary(report: &DetectionReport) {
    println!(
        "Coverage: {:.1}% of files ({} / {})",
        report.coverage_percent, report.supported_files, report.total_files
    );

    if report.excluded_files > 0 {
        println!("Excluded: {} files", report.excluded_files);
    }

    if report.walk_errors > 0 {
        println!("Walk errors: {}", report.walk_errors);
    }

    if !report.unrecognized_extensions.is_empty() {
        println!();
        println!("Unrecognized file types (not indexed):");

        let max_shown = 5;
        for ext_count in report.unrecognized_extensions.iter().take(max_shown) {
            println!("  .{:<8} {} files", ext_count.extension, ext_count.count);
        }

        if report.unrecognized_extensions.len() > max_shown {
            let rest = &report.unrecognized_extensions[max_shown..];
            let rest_count: usize = rest.iter().map(|e| e.count).sum();
            println!(
                "  ... and {} more extensions ({} files)",
                rest.len(),
                rest_count
            );
        }
    }
}

/// Check which indexers are available
fn check_indexers() -> Result<()> {
    let available = legend_indexer::orchestrate::check_available_indexers();
    println!("SCIP Indexer Availability:\n");

    for (header, bundled) in &[("Bundled (priority) indexers:", true), ("Additional indexers:", false)] {
        println!("{}", header);
        for &lang in Language::ALL {
            if lang.is_bundled() != *bundled { continue; }
            let status = if available.get(&lang).copied().unwrap_or(false) { "available" } else { "not found" };
            println!("  {:12} ({:20}) - {}", lang.display_name(), lang.scip_indexer(), status);
        }
        println!();
    }

    println!("To install missing indexers:");
    for &lang in Language::ALL {
        if !lang.is_bundled() { continue; }
        println!("  {:12} {}", lang.display_name(), lang.install_command());
    }

    Ok(())
}
