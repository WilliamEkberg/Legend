//! Language detection module
//!
//! Detects programming languages in a codebase by examining file extensions
//! and configuration files.

use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use tracing::warn;
use walkdir::WalkDir;

/// Supported programming languages with their SCIP indexer mappings
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Language {
    TypeScript,
    JavaScript,
    Python,
    CSharp,
    Java,
    Kotlin,
    Scala,
    Go,
    Rust,
    Ruby,
    Php,
    Cpp,
    C,
    Dart,
}

/// Config files that indicate language presence (exact filename match)
const CONFIG_FILES: &[(&str, Language)] = &[
    ("package.json", Language::JavaScript),
    ("tsconfig.json", Language::TypeScript),
    ("pyproject.toml", Language::Python),
    ("setup.py", Language::Python),
    ("requirements.txt", Language::Python),
    ("Pipfile", Language::Python),
    ("Cargo.toml", Language::Rust),
    ("go.mod", Language::Go),
    ("go.sum", Language::Go),
    ("pom.xml", Language::Java),
    ("build.gradle", Language::Java),
    ("build.gradle.kts", Language::Java),
    ("Gemfile", Language::Ruby),
    ("composer.json", Language::Php),
    ("pubspec.yaml", Language::Dart),
];

/// Static metadata for each language — replaces 8 separate match methods.
struct LanguageSpec {
    language: Language,
    display_name: &'static str,
    scip_indexer: &'static str,
    scip_output_stem: &'static str,
    is_bundled: bool,
    extensions: &'static [&'static str],
    install_command: &'static str,
    binary_names: &'static [&'static str],
    aliases: &'static [&'static str],
}

const SPECS: &[LanguageSpec] = &[
    LanguageSpec {
        language: Language::TypeScript,
        display_name: "TypeScript",
        scip_indexer: "scip-typescript",
        scip_output_stem: "typescript",
        is_bundled: true,
        extensions: &["ts", "tsx", "mts", "cts"],
        install_command: "npm install -g @sourcegraph/scip-typescript",
        binary_names: &["scip-typescript", "scip-ts"],
        aliases: &["typescript", "ts"],
    },
    LanguageSpec {
        language: Language::JavaScript,
        display_name: "JavaScript",
        scip_indexer: "scip-typescript",
        scip_output_stem: "javascript",
        is_bundled: true,
        extensions: &["js", "jsx", "mjs", "cjs"],
        install_command: "npm install -g @sourcegraph/scip-typescript",
        binary_names: &["scip-typescript", "scip-ts"],
        aliases: &["javascript", "js"],
    },
    LanguageSpec {
        language: Language::Python,
        display_name: "Python",
        scip_indexer: "scip-python",
        scip_output_stem: "python",
        is_bundled: true,
        extensions: &["py", "pyi", "pyw"],
        install_command: "pip install scip-python",
        binary_names: &["scip-python", "scip-py"],
        aliases: &["python", "py"],
    },
    LanguageSpec {
        language: Language::CSharp,
        display_name: "C#",
        scip_indexer: "scip-dotnet",
        scip_output_stem: "csharp",
        is_bundled: true,
        extensions: &["cs", "csx"],
        install_command: "dotnet tool install -g scip-dotnet",
        binary_names: &["scip-dotnet", "scip-csharp"],
        aliases: &["csharp", "c#", "cs"],
    },
    LanguageSpec {
        language: Language::Java,
        display_name: "Java",
        scip_indexer: "scip-java",
        scip_output_stem: "java",
        is_bundled: true,
        extensions: &["java"],
        install_command: "coursier install scip-java",
        binary_names: &["scip-java"],
        aliases: &["java"],
    },
    LanguageSpec {
        language: Language::Kotlin,
        display_name: "Kotlin",
        scip_indexer: "scip-java",
        scip_output_stem: "kotlin",
        is_bundled: true, // uses scip-java which IS bundled in Docker
        extensions: &["kt", "kts"],
        install_command: "coursier install scip-java",
        binary_names: &["scip-java"],
        aliases: &["kotlin", "kt"],
    },
    LanguageSpec {
        language: Language::Scala,
        display_name: "Scala",
        scip_indexer: "scip-java",
        scip_output_stem: "scala",
        is_bundled: true, // uses scip-java which IS bundled in Docker
        extensions: &["scala", "sc"],
        install_command: "coursier install scip-java",
        binary_names: &["scip-java"],
        aliases: &["scala"],
    },
    LanguageSpec {
        language: Language::Go,
        display_name: "Go",
        scip_indexer: "scip-go",
        scip_output_stem: "go",
        is_bundled: true,
        extensions: &["go"],
        install_command: "go install github.com/sourcegraph/scip-go@latest",
        binary_names: &["scip-go"],
        aliases: &["go", "golang"],
    },
    LanguageSpec {
        language: Language::Rust,
        display_name: "Rust",
        scip_indexer: "rust-analyzer",
        scip_output_stem: "rust",
        is_bundled: false,
        extensions: &["rs"],
        install_command: "cargo install scip-rust (via rust-analyzer)",
        binary_names: &["rust-analyzer"],
        aliases: &["rust", "rs"],
    },
    LanguageSpec {
        language: Language::Ruby,
        display_name: "Ruby",
        scip_indexer: "scip-ruby",
        scip_output_stem: "ruby",
        is_bundled: false,
        extensions: &["rb", "rake", "gemspec"],
        install_command: "gem install scip-ruby",
        binary_names: &["scip-ruby"],
        aliases: &["ruby", "rb"],
    },
    LanguageSpec {
        language: Language::Php,
        display_name: "PHP",
        scip_indexer: "scip-php",
        scip_output_stem: "php",
        is_bundled: true,
        extensions: &["php", "phtml", "php3", "php4", "php5", "phps"],
        install_command: "composer global require davidrjenni/scip-php",
        binary_names: &["scip-php"],
        aliases: &["php"],
    },
    LanguageSpec {
        language: Language::Cpp,
        display_name: "C++",
        scip_indexer: "scip-clang",
        scip_output_stem: "cpp",
        is_bundled: false,
        extensions: &["cpp", "cxx", "cc", "c++", "hpp", "hxx", "hh", "h++"],
        install_command: "See: https://github.com/sourcegraph/scip-clang",
        binary_names: &["scip-clang"],
        aliases: &["cpp", "c++"],
    },
    LanguageSpec {
        language: Language::C,
        display_name: "C",
        scip_indexer: "scip-clang",
        scip_output_stem: "c",
        is_bundled: false,
        extensions: &["c", "h"],
        install_command: "See: https://github.com/sourcegraph/scip-clang",
        binary_names: &["scip-clang"],
        aliases: &["c"],
    },
    LanguageSpec {
        language: Language::Dart,
        display_name: "Dart",
        scip_indexer: "scip-dart",
        scip_output_stem: "dart",
        is_bundled: false,
        extensions: &["dart"],
        install_command: "dart pub global activate scip_dart",
        binary_names: &["dart"], // invoked via `dart pub global run scip_dart`
        aliases: &["dart"],
    },
];

impl Language {
    /// All supported languages
    pub const ALL: &[Language] = &[
        Language::TypeScript,
        Language::JavaScript,
        Language::Python,
        Language::CSharp,
        Language::Java,
        Language::Kotlin,
        Language::Scala,
        Language::Go,
        Language::Rust,
        Language::Ruby,
        Language::Php,
        Language::Cpp,
        Language::C,
        Language::Dart,
    ];

    fn spec(&self) -> &'static LanguageSpec {
        SPECS.iter().find(|s| s.language == *self).unwrap()
    }

    pub fn scip_indexer(&self) -> &'static str { self.spec().scip_indexer }
    pub fn display_name(&self) -> &'static str { self.spec().display_name }
    pub fn scip_output_stem(&self) -> &'static str { self.spec().scip_output_stem }
    pub fn is_bundled(&self) -> bool { self.spec().is_bundled }
    pub fn extensions(&self) -> &'static [&'static str] { self.spec().extensions }
    pub fn install_command(&self) -> &'static str { self.spec().install_command }
    pub fn scip_binary_names(&self) -> &'static [&'static str] { self.spec().binary_names }

    /// Parse language from string
    pub fn parse(s: &str) -> Option<Self> {
        let lower = s.to_lowercase();
        SPECS.iter()
            .find(|spec| spec.aliases.contains(&lower.as_str()))
            .map(|s| s.language)
    }
}

/// Information about detected language presence
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LanguageInfo {
    pub language: Language,
    pub file_count: usize,
    pub config_files: Vec<PathBuf>,
}

/// Summary of detection coverage across the codebase
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DetectionReport {
    pub languages: Vec<LanguageInfo>,
    /// Every file walked (after directory filtering, before extension matching)
    pub total_files: usize,
    /// Files matching a known language extension
    pub supported_files: usize,
    /// Files skipped by exclude patterns
    pub excluded_files: usize,
    /// Extensions that were not recognized, sorted descending by count
    pub unrecognized_extensions: Vec<ExtensionCount>,
    /// Permission errors, broken symlinks, etc.
    pub walk_errors: usize,
    /// supported_files / total_files * 100
    pub coverage_percent: f64,
}

/// Count of files with a particular unrecognized extension
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExtensionCount {
    /// Extension without dot prefix, e.g. "json", "md", "yaml"
    pub extension: String,
    pub count: usize,
}

/// Language detector for codebases
pub struct LanguageDetector {
    exclude_patterns: Vec<glob::Pattern>,
}

impl LanguageDetector {
    /// Create a new language detector with exclusion patterns
    pub fn new(exclude_patterns: &[String]) -> Self {
        let patterns = exclude_patterns
            .iter()
            .filter_map(|p| glob::Pattern::new(p).ok())
            .collect();
        Self {
            exclude_patterns: patterns,
        }
    }

    /// Check if a path should be excluded.
    ///
    /// In addition to standard glob matching against the full relative path,
    /// this also does component-based matching: for patterns like `dirname/**`,
    /// any path containing `dirname` as a component is excluded. This ensures
    /// nested occurrences (e.g. `packages/foo/node_modules/bar/`) are caught.
    fn should_exclude(&self, path: &Path) -> bool {
        let path_str = path.to_string_lossy();
        let file_name = path.file_name().unwrap_or_default().to_str().unwrap_or("");

        self.exclude_patterns.iter().any(|p| {
            // Standard glob match against full relative path or filename
            if p.matches(&path_str) || p.matches(file_name) {
                return true;
            }

            // Component-based match: if the pattern looks like `dirname/**`,
            // check if any path component equals `dirname`.
            let pattern_str = p.as_str();
            if let Some(dirname) = pattern_str.strip_suffix("/**") {
                if !dirname.contains('/') {
                    return path.components().any(|c| {
                        c.as_os_str().to_str() == Some(dirname)
                    });
                }
            }

            false
        })
    }

    /// Detect all languages present in the codebase
    pub fn detect(&self, root_path: &Path) -> Result<DetectionReport> {
        let mut language_counts: HashMap<Language, usize> = HashMap::new();
        let mut config_files: HashMap<Language, Vec<PathBuf>> = HashMap::new();
        let mut unrecognized_map: HashMap<String, usize> = HashMap::new();
        let mut total_files: usize = 0;
        let mut excluded_files: usize = 0;
        let mut walk_errors: usize = 0;

        // Build extension to language mapping
        let ext_to_lang: HashMap<&str, Language> = Language::ALL
            .iter()
            .flat_map(|lang| lang.extensions().iter().map(move |ext| (*ext, *lang)))
            .collect();

        // Build config file lookup
        let config_lookup: HashMap<&str, Language> = CONFIG_FILES.iter().copied().collect();

        // Walk the directory tree
        let walker = WalkDir::new(root_path).follow_links(true).into_iter();
        for entry_result in walker {
            let entry = match entry_result {
                Ok(e) => e,
                Err(e) => {
                    walk_errors += 1;
                    if walk_errors <= 5 {
                        warn!("Skipping inaccessible path: {}", e);
                    }
                    continue;
                }
            };
            let path = entry.path();

            // Skip excluded paths (count excluded files)
            if let Ok(rel_path) = path.strip_prefix(root_path) {
                if self.should_exclude(rel_path) {
                    if path.is_file() {
                        excluded_files += 1;
                    }
                    continue;
                }
            }

            if !path.is_file() {
                continue;
            }

            total_files += 1;

            let file_name = path.file_name().and_then(|n| n.to_str()).unwrap_or("");

            // Check for config files that indicate language
            if let Some(&lang) = config_lookup.get(file_name) {
                config_files
                    .entry(lang)
                    .or_default()
                    .push(path.to_path_buf());
            }

            // Check for .csproj / .sln files (suffix match, not exact)
            if file_name.ends_with(".csproj") || file_name.ends_with(".sln") {
                config_files
                    .entry(Language::CSharp)
                    .or_default()
                    .push(path.to_path_buf());
            }

            // Count files by extension
            if let Some(ext) = path.extension().and_then(|e| e.to_str()) {
                if let Some(&lang) = ext_to_lang.get(ext) {
                    *language_counts.entry(lang).or_insert(0) += 1;
                } else {
                    *unrecognized_map.entry(ext.to_lowercase()).or_insert(0) += 1;
                }
            } else {
                // Files with no extension (e.g. Makefile, Dockerfile)
                *unrecognized_map.entry(String::new()).or_insert(0) += 1;
            }
        }

        if walk_errors > 0 {
            warn!(
                "Skipped {} inaccessible entries during directory walk (permission errors, broken symlinks, etc.)",
                walk_errors
            );
        }

        // Build language result
        let mut languages: Vec<LanguageInfo> = language_counts
            .into_iter()
            .map(|(language, file_count)| LanguageInfo {
                language,
                file_count,
                config_files: config_files.remove(&language).unwrap_or_default(),
            })
            .collect();

        // Sort by file count (descending)
        languages.sort_by(|a, b| b.file_count.cmp(&a.file_count));

        let supported_files: usize = languages.iter().map(|l| l.file_count).sum();

        // Build unrecognized extensions list, sorted desc by count
        let mut unrecognized_extensions: Vec<ExtensionCount> = unrecognized_map
            .into_iter()
            .filter(|(ext, _)| !ext.is_empty()) // skip no-extension files from the named list
            .map(|(extension, count)| ExtensionCount { extension, count })
            .collect();
        unrecognized_extensions.sort_by(|a, b| b.count.cmp(&a.count));

        let coverage_percent = if total_files > 0 {
            (supported_files as f64 / total_files as f64) * 100.0
        } else {
            0.0
        };

        Ok(DetectionReport {
            languages,
            total_files,
            supported_files,
            excluded_files,
            unrecognized_extensions,
            walk_errors,
            coverage_percent,
        })
    }

    /// Filter detected languages to only include specified ones
    pub fn filter_languages(
        detected: Vec<LanguageInfo>,
        filter: &[String],
    ) -> Vec<LanguageInfo> {
        if filter.is_empty() {
            return detected;
        }

        let filter_set: HashSet<Language> = filter
            .iter()
            .filter_map(|s| Language::parse(s))
            .collect();

        detected
            .into_iter()
            .filter(|info| filter_set.contains(&info.language))
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;
    use std::fs;

    #[test]
    fn test_language_extensions() {
        assert!(Language::TypeScript.extensions().contains(&"ts"));
        assert!(Language::TypeScript.extensions().contains(&"tsx"));
        assert!(Language::Python.extensions().contains(&"py"));
    }

    #[test]
    fn test_language_from_str() {
        assert_eq!(Language::parse("typescript"), Some(Language::TypeScript));
        assert_eq!(Language::parse("ts"), Some(Language::TypeScript));
        assert_eq!(Language::parse("python"), Some(Language::Python));
        assert_eq!(Language::parse("py"), Some(Language::Python));
        assert_eq!(Language::parse("unknown"), None);
    }

    #[test]
    fn test_detect_typescript() -> Result<()> {
        let temp_dir = TempDir::new()?;
        fs::write(temp_dir.path().join("index.ts"), "export const x = 1;")?;
        fs::write(temp_dir.path().join("app.tsx"), "export const App = () => <div/>;")?;
        fs::write(temp_dir.path().join("tsconfig.json"), "{}")?;

        let detector = LanguageDetector::new(&[]);
        let report = detector.detect(temp_dir.path())?;

        assert!(!report.languages.is_empty());
        let ts_info = report.languages.iter().find(|i| i.language == Language::TypeScript);
        assert!(ts_info.is_some());
        assert_eq!(ts_info.unwrap().file_count, 2);

        Ok(())
    }

    #[test]
    fn test_exclude_patterns() -> Result<()> {
        let temp_dir = TempDir::new()?;
        fs::create_dir_all(temp_dir.path().join("node_modules"))?;
        fs::write(temp_dir.path().join("index.ts"), "export const x = 1;")?;
        fs::write(
            temp_dir.path().join("node_modules/dep.ts"),
            "export const y = 2;",
        )?;

        let detector = LanguageDetector::new(&["node_modules/**".to_string()]);
        let report = detector.detect(temp_dir.path())?;

        let ts_info = report.languages.iter().find(|i| i.language == Language::TypeScript);
        assert!(ts_info.is_some());
        assert_eq!(ts_info.unwrap().file_count, 1);

        Ok(())
    }
}
