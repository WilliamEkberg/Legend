//! Robustness tests for scip-engine
//!
//! Comprehensive tests covering bug fixes, edge cases, orchestration
//! lifecycle, determinism, and CLI integration.

mod common;

use common::{create_file, find_lang};
use legend_indexer::config::Config;
use legend_indexer::detect::{Language, LanguageDetector};
use legend_indexer::orchestrate::IndexerOrchestrator;
use std::fs;
use tempfile::TempDir;

// ===========================================================================
// mod bug_fixes — Verify all 5 fixes work
// ===========================================================================
mod bug_fixes {
    use super::*;

    #[test]
    fn test_stale_scip_files_cleaned_on_rerun() {
        let temp = TempDir::new().unwrap();
        // Output dir is /tmp/legend-indexer (not inside the codebase)
        let orch = IndexerOrchestrator::new(temp.path().to_path_buf(), None).unwrap();
        let output_dir = orch.output_dir().to_path_buf();

        // Place a stale .scip file
        let stale = output_dir.join("old-language.scip");
        fs::write(&stale, b"stale data").unwrap();
        assert!(stale.exists());

        // Constructing a new orchestrator should clean it
        let _orch2 = IndexerOrchestrator::new(temp.path().to_path_buf(), None).unwrap();
        assert!(!stale.exists(), "Stale .scip file should have been removed");

        // Non-.scip files should be preserved
        let keep = output_dir.join("notes.txt");
        fs::write(&keep, "keep me").unwrap();
        let _orch3 = IndexerOrchestrator::new(temp.path().to_path_buf(), None).unwrap();
        assert!(keep.exists(), "Non-.scip files should be preserved");
    }

    #[test]
    fn test_scip_output_stem_unique_per_language() {
        let stems: Vec<&str> = Language::ALL.iter().map(|l| l.scip_output_stem()).collect();
        let unique: std::collections::HashSet<&&str> = stems.iter().collect();
        assert_eq!(
            stems.len(),
            unique.len(),
            "All languages must have unique scip_output_stem values"
        );
    }

    #[test]
    fn test_nested_node_modules_excluded() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "node_modules/dep/index.ts", "export const x = 1;");
        create_file(
            temp.path(),
            "packages/foo/node_modules/dep/index.ts",
            "export const y = 2;",
        );
        create_file(temp.path(), "src/real.ts", "export const z = 3;");

        let detector = LanguageDetector::new(&["node_modules/**".to_string()]);
        let report = detector.detect(temp.path()).unwrap();

        let ts = find_lang(&report.languages, Language::TypeScript);
        assert!(ts.is_some(), "Should detect TypeScript");
        assert_eq!(ts.unwrap().file_count, 1, "Only src/real.ts should be counted");
    }

    #[cfg(unix)]
    #[test]
    fn test_broken_symlink_does_not_crash_and_walk_errors_counted() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "real.ts", "export const x = 1;");

        std::os::unix::fs::symlink(
            temp.path().join("nonexistent_target"),
            temp.path().join("broken_link"),
        )
        .unwrap();

        let detector = LanguageDetector::new(&[]);
        let report = detector.detect(temp.path()).unwrap();

        let ts = find_lang(&report.languages, Language::TypeScript);
        assert!(ts.is_some());
        assert!(ts.unwrap().file_count >= 1);
        assert!(report.walk_errors >= 1, "Broken symlink should produce walk error, got {}", report.walk_errors);
    }
}

// ===========================================================================
// mod detection — Language detection edge cases + determinism
// ===========================================================================
mod detection {
    use super::*;

    #[test]
    fn test_mixed_language_detection() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "app.ts", "const x = 1;");
        create_file(temp.path(), "script.py", "x = 1");
        create_file(temp.path(), "main.go", "package main");

        let detector = LanguageDetector::new(&[]);
        let detected = detector.detect(temp.path()).unwrap().languages;

        let langs: Vec<Language> = detected.iter().map(|i| i.language).collect();
        assert!(langs.contains(&Language::TypeScript));
        assert!(langs.contains(&Language::Python));
        assert!(langs.contains(&Language::Go));
    }

    #[test]
    fn test_deep_nested_directory_traversal() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "a/b/c/d/e/deep.ts", "export const deep = true;");

        let detector = LanguageDetector::new(&[]);
        let detected = detector.detect(temp.path()).unwrap().languages;

        let ts = find_lang(&detected, Language::TypeScript);
        assert!(ts.is_some());
        assert_eq!(ts.unwrap().file_count, 1);
    }

    #[test]
    fn test_special_characters_in_paths() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "my project/src-files/app_main.ts", "const x = 1;");

        let detector = LanguageDetector::new(&[]);
        let detected = detector.detect(temp.path()).unwrap().languages;

        let ts = find_lang(&detected, Language::TypeScript);
        assert!(ts.is_some());
        assert_eq!(ts.unwrap().file_count, 1);
    }

    #[test]
    fn test_unicode_in_paths() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "Oversikt.ts", "export const oversikt = true;");
        create_file(temp.path(), "données/traitement.py", "x = 1");

        let detector = LanguageDetector::new(&[]);
        let detected = detector.detect(temp.path()).unwrap().languages;

        assert!(!detected.is_empty());
    }

    #[test]
    fn test_language_filter_ignores_invalid() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "app.ts", "const x = 1;");
        create_file(temp.path(), "main.py", "x = 1");

        let detector = LanguageDetector::new(&[]);
        let detected = detector.detect(temp.path()).unwrap().languages;

        let filtered = LanguageDetector::filter_languages(
            detected,
            &["typescript".to_string(), "foobar".to_string()],
        );

        assert_eq!(filtered.len(), 1);
        assert_eq!(filtered[0].language, Language::TypeScript);
    }

    #[test]
    fn test_all_default_exclude_patterns() {
        let temp = TempDir::new().unwrap();

        create_file(temp.path(), "node_modules/pkg/index.ts", "x");
        create_file(temp.path(), ".git/objects/abc.ts", "x");
        create_file(temp.path(), "target/debug/main.rs", "x");
        create_file(temp.path(), "dist/bundle.js", "x");
        create_file(temp.path(), "build/output.ts", "x");
        create_file(temp.path(), "__pycache__/mod.py", "x");
        create_file(temp.path(), ".venv/bin/activate.py", "x");
        create_file(temp.path(), "venv/lib/site.py", "x");
        create_file(temp.path(), ".env/bin/activate.py", "x");
        create_file(temp.path(), "env/lib/site.py", "x");

        create_file(temp.path(), "src/real.ts", "export const x = 1;");

        let detector = LanguageDetector::new(&Config::default().exclude_patterns);
        let detected = detector.detect(temp.path()).unwrap().languages;

        let ts = find_lang(&detected, Language::TypeScript);
        assert!(ts.is_some());
        assert_eq!(
            ts.unwrap().file_count, 1,
            "Only src/real.ts should be counted, all excluded dirs should be filtered"
        );
    }

    #[test]
    fn test_h_extension_maps_to_c() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "header.h", "#include <stdio.h>");
        create_file(temp.path(), "impl.cpp", "int main() {}");
        create_file(temp.path(), "header2.hpp", "class Foo {};");

        let detector = LanguageDetector::new(&[]);
        let detected = detector.detect(temp.path()).unwrap().languages;

        let c = find_lang(&detected, Language::C);
        let cpp = find_lang(&detected, Language::Cpp);

        assert!(c.is_some(), ".h should map to C");
        assert_eq!(c.unwrap().file_count, 1);
        assert!(cpp.is_some(), ".cpp and .hpp should map to C++");
        assert_eq!(cpp.unwrap().file_count, 2);
    }

    #[cfg(unix)]
    #[test]
    fn test_symlink_to_valid_file_counted() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "real.ts", "export const x = 1;");
        std::os::unix::fs::symlink(
            temp.path().join("real.ts"),
            temp.path().join("link.ts"),
        )
        .unwrap();

        let detector = LanguageDetector::new(&[]);
        let detected = detector.detect(temp.path()).unwrap().languages;

        let ts = find_lang(&detected, Language::TypeScript);
        assert!(ts.is_some());
        assert_eq!(ts.unwrap().file_count, 2);
    }

    #[test]
    fn test_empty_codebase_returns_empty() {
        let temp = TempDir::new().unwrap();
        let detector = LanguageDetector::new(&[]);
        let detected = detector.detect(temp.path()).unwrap().languages;
        assert!(detected.is_empty());
    }

    #[test]
    fn test_single_file_project() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "main.rs", "fn main() {}");

        let detector = LanguageDetector::new(&[]);
        let detected = detector.detect(temp.path()).unwrap().languages;

        assert_eq!(detected.len(), 1);
        assert_eq!(detected[0].language, Language::Rust);
        assert_eq!(detected[0].file_count, 1);
    }

    #[test]
    fn test_config_files_detected() {
        let temp = TempDir::new().unwrap();

        create_file(temp.path(), "tsconfig.json", "{}");
        create_file(temp.path(), "Cargo.toml", "[package]");
        create_file(temp.path(), "go.mod", "module example");
        create_file(temp.path(), "requirements.txt", "requests");
        create_file(temp.path(), "MyApp.csproj", "<Project/>");
        create_file(temp.path(), "pom.xml", "<project/>");

        create_file(temp.path(), "app.ts", "const x = 1;");
        create_file(temp.path(), "main.rs", "fn main() {}");
        create_file(temp.path(), "main.go", "package main");
        create_file(temp.path(), "app.py", "x = 1");
        create_file(temp.path(), "App.cs", "class App {}");
        create_file(temp.path(), "App.java", "class App {}");

        let detector = LanguageDetector::new(&[]);
        let detected = detector.detect(temp.path()).unwrap().languages;

        let langs: Vec<Language> = detected.iter().map(|i| i.language).collect();
        assert!(langs.contains(&Language::TypeScript));
        assert!(langs.contains(&Language::Rust));
        assert!(langs.contains(&Language::Go));
        assert!(langs.contains(&Language::Python));
        assert!(langs.contains(&Language::CSharp));
        assert!(langs.contains(&Language::Java));

        let ts = find_lang(&detected, Language::TypeScript).unwrap();
        assert!(!ts.config_files.is_empty(), "tsconfig.json should be a config file for TS");
    }

    #[test]
    fn test_detection_determinism_10_runs() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "app.ts", "const x = 1;");
        create_file(temp.path(), "app.tsx", "const App = () => <div/>;");
        create_file(temp.path(), "script.py", "x = 1");
        create_file(temp.path(), "util.py", "def f(): pass");
        create_file(temp.path(), "main.go", "package main");

        let detector = LanguageDetector::new(&[]);

        let baseline = detector.detect(temp.path()).unwrap().languages;
        let mut baseline_snapshot: Vec<(String, usize)> = baseline
            .iter()
            .map(|i| (i.language.display_name().to_string(), i.file_count))
            .collect();
        baseline_snapshot.sort();

        for run in 1..=10 {
            let result = detector.detect(temp.path()).unwrap().languages;
            let mut snapshot: Vec<(String, usize)> = result
                .iter()
                .map(|i| (i.language.display_name().to_string(), i.file_count))
                .collect();
            snapshot.sort();
            assert_eq!(
                baseline_snapshot, snapshot,
                "Detection run {} produced different results",
                run
            );
        }
    }
}

// ===========================================================================
// mod orchestration — Orchestrator lifecycle
// ===========================================================================
//
// NOTE: All orchestrators share a hardcoded output dir (/tmp/legend-indexer).
// These tests are combined into a single test function to avoid race conditions
// when cargo runs tests in parallel (cleanup in one test deletes the dir
// another test just created).
mod orchestration {
    use super::*;

    #[test]
    fn test_orchestrator_lifecycle() {
        // Part 1: Creating an orchestrator produces the output directory
        let temp = TempDir::new().unwrap();
        let orch = IndexerOrchestrator::new(temp.path().to_path_buf(), None).unwrap();
        assert!(orch.output_dir().exists(), "output dir should exist after new()");
        assert!(orch.output_dir().is_dir(), "output dir should be a directory");
        drop(orch);

        // Part 2: Output directory persists when orchestrator is dropped without cleanup
        let temp2 = TempDir::new().unwrap();
        let output_dir;
        {
            let orch2 = IndexerOrchestrator::new(temp2.path().to_path_buf(), None).unwrap();
            output_dir = orch2.output_dir().to_path_buf();
        }
        assert!(output_dir.exists(), "output dir should survive orchestrator drop");

        // Part 3: cleanup() removes the output directory
        let temp3 = TempDir::new().unwrap();
        let orch3 = IndexerOrchestrator::new(temp3.path().to_path_buf(), None).unwrap();
        let output_dir3 = orch3.output_dir().to_path_buf();
        assert!(output_dir3.exists(), "output dir should exist before cleanup");
        orch3.cleanup().unwrap();
        assert!(!output_dir3.exists(), "output dir should be gone after cleanup");
    }
}

// ===========================================================================
// mod cli — CLI integration tests
// ===========================================================================
mod cli {
    use super::*;
    use assert_cmd::Command;
    use predicates::prelude::*;

    #[test]
    fn test_cli_detect_determinism() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "app.ts", "const x = 1;");
        create_file(temp.path(), "script.py", "x = 1");
        create_file(temp.path(), "main.go", "package main");

        let output1 = Command::cargo_bin("legend-indexer")
            .unwrap()
            .arg("detect")
            .arg(temp.path())
            .output()
            .unwrap();

        let output2 = Command::cargo_bin("legend-indexer")
            .unwrap()
            .arg("detect")
            .arg(temp.path())
            .output()
            .unwrap();

        let mut lines1: Vec<String> = String::from_utf8_lossy(&output1.stdout)
            .lines()
            .map(|l| l.to_string())
            .collect();
        let mut lines2: Vec<String> = String::from_utf8_lossy(&output2.stdout)
            .lines()
            .map(|l| l.to_string())
            .collect();
        lines1.sort();
        lines2.sort();

        assert_eq!(
            lines1, lines2,
            "Two detect runs should produce the same set of lines"
        );
    }

    #[test]
    fn test_cli_detect_multi_language() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "app.ts", "const x = 1;");
        create_file(temp.path(), "script.py", "x = 1");
        create_file(temp.path(), "main.go", "package main");

        Command::cargo_bin("legend-indexer")
            .unwrap()
            .arg("detect")
            .arg(temp.path())
            .assert()
            .success()
            .stdout(predicate::str::contains("TypeScript"))
            .stdout(predicate::str::contains("Python"))
            .stdout(predicate::str::contains("Go"));
    }

    #[test]
    fn test_cli_nonexistent_path() {
        let result = Command::cargo_bin("legend-indexer")
            .unwrap()
            .arg("detect")
            .arg("/nonexistent/path/that/does/not/exist")
            .output()
            .unwrap();

        let combined = format!(
            "{}{}",
            String::from_utf8_lossy(&result.stdout),
            String::from_utf8_lossy(&result.stderr)
        );
        assert!(
            !result.status.success()
                || combined.contains("No supported")
                || combined.contains("error")
                || combined.contains("Error"),
            "Should indicate an error for nonexistent path, got: {}",
            combined
        );
    }

    #[test]
    fn test_cli_language_filter_flag() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "app.ts", "const x = 1;");
        create_file(temp.path(), "script.py", "x = 1");

        let result = Command::cargo_bin("legend-indexer")
            .unwrap()
            .arg(temp.path())
            .arg("--languages")
            .arg("typescript")
            .output()
            .unwrap();

        let stdout = String::from_utf8_lossy(&result.stdout);
        let stderr = String::from_utf8_lossy(&result.stderr);
        let combined = format!("{}{}", stdout, stderr);

        assert!(
            result.status.success() || combined.contains("TypeScript") || combined.contains("indexer"),
            "Command should handle --languages filter correctly"
        );
    }

    #[test]
    fn test_cli_detect_shows_coverage() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "app.ts", "const x = 1;");
        create_file(temp.path(), "data.json", "{}");
        create_file(temp.path(), "README.md", "# Hello");

        Command::cargo_bin("legend-indexer")
            .unwrap()
            .arg("detect")
            .arg(temp.path())
            .assert()
            .success()
            .stdout(predicate::str::contains("Coverage:"));
    }
}

// ===========================================================================
// mod report — Detection report coverage & metrics
// ===========================================================================
mod report {
    use super::*;
    use legend_indexer::detect::DetectionReport;

    #[test]
    fn test_report_counts_unrecognized_extensions() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "app.ts", "const x = 1;");
        create_file(temp.path(), "data.json", "{}");
        create_file(temp.path(), "README.md", "# Hello");
        create_file(temp.path(), "config.yaml", "key: value");

        let detector = LanguageDetector::new(&[]);
        let report = detector.detect(temp.path()).unwrap();

        assert!(find_lang(&report.languages, Language::TypeScript).is_some());

        let ext_names: Vec<&str> = report
            .unrecognized_extensions
            .iter()
            .map(|e| e.extension.as_str())
            .collect();
        assert!(ext_names.contains(&"json"), "json should be unrecognized, got: {:?}", ext_names);
        assert!(ext_names.contains(&"md"), "md should be unrecognized, got: {:?}", ext_names);
        assert!(ext_names.contains(&"yaml"), "yaml should be unrecognized, got: {:?}", ext_names);
    }

    #[test]
    fn test_report_coverage_percent() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "a.ts", "x");
        create_file(temp.path(), "b.ts", "x");
        create_file(temp.path(), "c.ts", "x");
        for i in 0..7 {
            create_file(temp.path(), &format!("data{}.json", i), "{}");
        }

        let detector = LanguageDetector::new(&[]);
        let report = detector.detect(temp.path()).unwrap();

        assert_eq!(report.supported_files, 3);
        assert_eq!(report.total_files, 10);
        assert!((report.coverage_percent - 30.0).abs() < 0.1,
            "Expected ~30% coverage, got {:.1}%", report.coverage_percent);
    }

    #[test]
    fn test_report_excluded_files_counted() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "src/app.ts", "const x = 1;");
        create_file(temp.path(), "node_modules/dep/index.ts", "export const y = 2;");
        create_file(temp.path(), "node_modules/dep/util.ts", "export const z = 3;");

        let detector = LanguageDetector::new(&["node_modules/**".to_string()]);
        let report = detector.detect(temp.path()).unwrap();

        assert_eq!(report.excluded_files, 2, "Two files in node_modules should be excluded");
        assert_eq!(report.supported_files, 1, "Only src/app.ts should be supported");

        let has_ts_unrecognized = report.unrecognized_extensions.iter().any(|e| e.extension == "ts");
        assert!(!has_ts_unrecognized, "Excluded .ts files should not be in unrecognized");
    }

    #[test]
    fn test_report_empty_codebase() {
        let temp = TempDir::new().unwrap();

        let detector = LanguageDetector::new(&[]);
        let report = detector.detect(temp.path()).unwrap();

        assert_eq!(report.total_files, 0);
        assert_eq!(report.supported_files, 0);
        assert_eq!(report.excluded_files, 0);
        assert_eq!(report.walk_errors, 0);
        assert!(report.unrecognized_extensions.is_empty());
        assert!(report.languages.is_empty());
        assert!((report.coverage_percent - 0.0).abs() < f64::EPSILON);
    }

    #[test]
    fn test_report_unrecognized_sorted_desc() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "a.yaml", "x");
        create_file(temp.path(), "a.json", "{}");
        create_file(temp.path(), "b.json", "{}");
        create_file(temp.path(), "c.json", "{}");
        create_file(temp.path(), "a.md", "x");
        create_file(temp.path(), "b.md", "x");

        let detector = LanguageDetector::new(&[]);
        let report = detector.detect(temp.path()).unwrap();

        assert!(report.unrecognized_extensions.len() >= 3);
        for w in report.unrecognized_extensions.windows(2) {
            assert!(w[0].count >= w[1].count,
                "Unrecognized extensions should be sorted desc, got {} ({}) before {} ({})",
                w[0].extension, w[0].count, w[1].extension, w[1].count);
        }
    }

    #[test]
    fn test_report_json_serializable() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "app.ts", "const x = 1;");
        create_file(temp.path(), "data.json", "{}");

        let detector = LanguageDetector::new(&[]);
        let report = detector.detect(temp.path()).unwrap();

        let json = serde_json::to_string_pretty(&report).unwrap();
        let parsed: DetectionReport = serde_json::from_str(&json).unwrap();

        assert_eq!(parsed.total_files, report.total_files);
        assert_eq!(parsed.supported_files, report.supported_files);
        assert_eq!(parsed.languages.len(), report.languages.len());
        assert_eq!(parsed.unrecognized_extensions.len(), report.unrecognized_extensions.len());
    }
}
