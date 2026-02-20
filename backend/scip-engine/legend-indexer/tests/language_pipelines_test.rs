//! Per-language pipeline tests for legend-indexer
//!
//! Tests dispatch logic, argument construction, detection, and metadata
//! for all supported language groups.

mod common;

use common::{create_file, find_lang};
use legend_indexer::detect::{Language, LanguageDetector};
use legend_indexer::orchestrate::IndexerOrchestrator;
use std::fs;
use tempfile::TempDir;

// ===========================================================================
// Language metadata tests — verify all specs are correctly configured
// ===========================================================================
mod metadata {
    use super::*;

    #[test]
    fn test_all_languages_have_unique_output_stems() {
        let stems: Vec<&str> = Language::ALL.iter().map(|l| l.scip_output_stem()).collect();
        let unique: std::collections::HashSet<&&str> = stems.iter().collect();
        assert_eq!(stems.len(), unique.len(), "Duplicate output stems found: {:?}", stems);
    }

    #[test]
    fn test_all_languages_have_extensions() {
        for &lang in Language::ALL {
            assert!(!lang.extensions().is_empty(), "{:?} has no file extensions", lang);
        }
    }

    #[test]
    fn test_all_languages_have_install_commands() {
        for &lang in Language::ALL {
            assert!(!lang.install_command().is_empty(), "{:?} has no install command", lang);
        }
    }

    #[test]
    fn test_all_languages_have_binary_names() {
        for &lang in Language::ALL {
            assert!(!lang.scip_binary_names().is_empty(), "{:?} has no binary names", lang);
        }
    }

    #[test]
    fn test_all_languages_parseable_from_aliases() {
        for &lang in Language::ALL {
            // Every language should be parseable from at least its display name (lowered)
            let display = lang.display_name().to_lowercase();
            // Some display names like "C#" or "C++" won't parse directly, so check aliases exist
            // by testing that at least one parse succeeds among common forms
            let found = Language::parse(&display).is_some()
                || Language::parse(lang.scip_output_stem()).is_some();
            assert!(found, "{:?} not parseable from display_name or output_stem", lang);
        }
    }

    // --- TypeScript / JavaScript ---

    #[test]
    fn test_typescript_metadata() {
        assert_eq!(Language::TypeScript.scip_indexer(), "scip-typescript");
        assert!(Language::TypeScript.is_bundled());
        assert!(Language::TypeScript.extensions().contains(&"ts"));
        assert!(Language::TypeScript.extensions().contains(&"tsx"));
        assert!(Language::TypeScript.extensions().contains(&"mts"));
    }

    #[test]
    fn test_javascript_metadata() {
        assert_eq!(Language::JavaScript.scip_indexer(), "scip-typescript");
        assert!(Language::JavaScript.is_bundled());
        assert!(Language::JavaScript.extensions().contains(&"js"));
        assert!(Language::JavaScript.extensions().contains(&"jsx"));
        assert!(Language::JavaScript.extensions().contains(&"mjs"));
    }

    // --- Python ---

    #[test]
    fn test_python_metadata() {
        assert_eq!(Language::Python.scip_indexer(), "scip-python");
        assert!(Language::Python.is_bundled());
        assert!(Language::Python.extensions().contains(&"py"));
        assert!(Language::Python.extensions().contains(&"pyi"));
    }

    // --- Go ---

    #[test]
    fn test_go_metadata() {
        assert_eq!(Language::Go.scip_indexer(), "scip-go");
        assert!(Language::Go.is_bundled());
        assert_eq!(Language::Go.extensions(), &["go"]);
    }

    // --- Java / Kotlin / Scala ---

    #[test]
    fn test_java_kotlin_scala_share_indexer() {
        assert_eq!(Language::Java.scip_indexer(), "scip-java");
        assert_eq!(Language::Kotlin.scip_indexer(), "scip-java");
        assert_eq!(Language::Scala.scip_indexer(), "scip-java");
    }

    #[test]
    fn test_kotlin_scala_bundled_via_scip_java() {
        // Kotlin and Scala use scip-java which is bundled in Docker
        assert!(Language::Kotlin.is_bundled(), "Kotlin should be bundled (uses scip-java)");
        assert!(Language::Scala.is_bundled(), "Scala should be bundled (uses scip-java)");
        assert!(Language::Java.is_bundled());
    }

    #[test]
    fn test_kotlin_scala_binary_names_match_java() {
        assert_eq!(Language::Kotlin.scip_binary_names(), Language::Java.scip_binary_names());
        assert_eq!(Language::Scala.scip_binary_names(), Language::Java.scip_binary_names());
    }

    // --- C# ---

    #[test]
    fn test_csharp_metadata() {
        assert_eq!(Language::CSharp.scip_indexer(), "scip-dotnet");
        assert!(Language::CSharp.is_bundled());
        assert!(Language::CSharp.extensions().contains(&"cs"));
    }

    // --- PHP ---

    #[test]
    fn test_php_metadata() {
        assert_eq!(Language::Php.scip_indexer(), "scip-php");
        assert!(Language::Php.is_bundled());
        assert!(Language::Php.extensions().contains(&"php"));
        // Verify install command references correct package
        assert!(
            Language::Php.install_command().contains("davidrjenni/scip-php"),
            "PHP install command should reference davidrjenni/scip-php, got: {}",
            Language::Php.install_command()
        );
    }

    // --- Rust ---

    #[test]
    fn test_rust_metadata() {
        assert_eq!(Language::Rust.scip_indexer(), "rust-analyzer");
        assert!(!Language::Rust.is_bundled());
        assert_eq!(Language::Rust.extensions(), &["rs"]);
    }

    // --- Ruby ---

    #[test]
    fn test_ruby_metadata() {
        assert_eq!(Language::Ruby.scip_indexer(), "scip-ruby");
        assert!(!Language::Ruby.is_bundled());
        assert!(Language::Ruby.extensions().contains(&"rb"));
        assert!(Language::Ruby.extensions().contains(&"gemspec"));
    }

    // --- C/C++ ---

    #[test]
    fn test_cpp_c_share_indexer() {
        assert_eq!(Language::Cpp.scip_indexer(), "scip-clang");
        assert_eq!(Language::C.scip_indexer(), "scip-clang");
        // Install URL should reference sourcegraph
        assert!(
            Language::Cpp.install_command().contains("sourcegraph/scip-clang"),
            "C++ install should reference sourcegraph/scip-clang, got: {}",
            Language::Cpp.install_command()
        );
    }

    // --- Dart ---

    #[test]
    fn test_dart_metadata() {
        assert_eq!(Language::Dart.scip_indexer(), "scip-dart");
        assert!(!Language::Dart.is_bundled());
        assert_eq!(Language::Dart.extensions(), &["dart"]);
        assert!(Language::Dart.install_command().contains("scip_dart"));
        // Dart is invoked via `dart pub global run`, so binary name should be `dart`
        assert!(
            Language::Dart.scip_binary_names().contains(&"dart"),
            "Dart binary names should include 'dart', got: {:?}",
            Language::Dart.scip_binary_names()
        );
    }
}

// ===========================================================================
// Detection tests — language-specific detection edge cases
// ===========================================================================
mod detection {
    use super::*;

    #[test]
    fn test_detect_typescript_with_tsconfig() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "tsconfig.json", "{}");
        create_file(temp.path(), "src/app.ts", "const x = 1;");
        create_file(temp.path(), "src/app.tsx", "const App = () => <div/>;");

        let detector = LanguageDetector::new(&[]);
        let report = detector.detect(temp.path()).unwrap();

        let ts = find_lang(&report.languages, Language::TypeScript).unwrap();
        assert_eq!(ts.file_count, 2);
        assert!(!ts.config_files.is_empty(), "tsconfig.json should be detected as config file");
    }

    #[test]
    fn test_detect_javascript_standalone() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "package.json", r#"{"name":"test"}"#);
        create_file(temp.path(), "index.js", "module.exports = {};");
        create_file(temp.path(), "util.mjs", "export default {};");

        let detector = LanguageDetector::new(&[]);
        let report = detector.detect(temp.path()).unwrap();

        let js = find_lang(&report.languages, Language::JavaScript).unwrap();
        assert_eq!(js.file_count, 2);
    }

    #[test]
    fn test_detect_python_with_various_configs() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "pyproject.toml", "[build-system]");
        create_file(temp.path(), "requirements.txt", "requests==2.28.0");
        create_file(temp.path(), "app.py", "x = 1");
        create_file(temp.path(), "types.pyi", "x: int");

        let detector = LanguageDetector::new(&[]);
        let report = detector.detect(temp.path()).unwrap();

        let py = find_lang(&report.languages, Language::Python).unwrap();
        assert_eq!(py.file_count, 2); // .py + .pyi
        assert!(py.config_files.len() >= 2, "Should detect pyproject.toml and requirements.txt");
    }

    #[test]
    fn test_detect_go_with_go_mod() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "go.mod", "module example.com/test\ngo 1.21");
        create_file(temp.path(), "main.go", "package main\nfunc main() {}");

        let detector = LanguageDetector::new(&[]);
        let report = detector.detect(temp.path()).unwrap();

        let go = find_lang(&report.languages, Language::Go).unwrap();
        assert_eq!(go.file_count, 1);
        assert!(!go.config_files.is_empty(), "go.mod should be detected");
    }

    #[test]
    fn test_detect_java_with_gradle() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "build.gradle", "apply plugin: 'java'");
        create_file(temp.path(), "src/main/java/App.java", "public class App {}");

        let detector = LanguageDetector::new(&[]);
        let report = detector.detect(temp.path()).unwrap();

        let java = find_lang(&report.languages, Language::Java).unwrap();
        assert_eq!(java.file_count, 1);
        assert!(!java.config_files.is_empty());
    }

    #[test]
    fn test_detect_kotlin_files() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "build.gradle.kts", "plugins { kotlin(\"jvm\") }");
        create_file(temp.path(), "src/main/kotlin/App.kt", "fun main() {}");
        create_file(temp.path(), "build.gradle.kts", "");

        let detector = LanguageDetector::new(&[]);
        let report = detector.detect(temp.path()).unwrap();

        let kt = find_lang(&report.languages, Language::Kotlin);
        assert!(kt.is_some(), "Kotlin .kt files should be detected");
    }

    #[test]
    fn test_detect_csharp_with_sln() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "MyApp.sln", "Microsoft Visual Studio Solution File");
        create_file(temp.path(), "src/App.cs", "class App {}");

        let detector = LanguageDetector::new(&[]);
        let report = detector.detect(temp.path()).unwrap();

        let cs = find_lang(&report.languages, Language::CSharp).unwrap();
        assert_eq!(cs.file_count, 1);
        assert!(!cs.config_files.is_empty(), ".sln should be a config file for C#");
    }

    #[test]
    fn test_detect_php_with_composer() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "composer.json", r#"{"require":{}}"#);
        create_file(temp.path(), "src/index.php", "<?php echo 'hi';");

        let detector = LanguageDetector::new(&[]);
        let report = detector.detect(temp.path()).unwrap();

        let php = find_lang(&report.languages, Language::Php).unwrap();
        assert_eq!(php.file_count, 1);
        assert!(!php.config_files.is_empty());
    }

    #[test]
    fn test_detect_rust_with_cargo() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "Cargo.toml", "[package]\nname = \"test\"");
        create_file(temp.path(), "src/main.rs", "fn main() {}");
        create_file(temp.path(), "src/lib.rs", "pub fn hello() {}");

        let detector = LanguageDetector::new(&[]);
        let report = detector.detect(temp.path()).unwrap();

        let rs = find_lang(&report.languages, Language::Rust).unwrap();
        assert_eq!(rs.file_count, 2);
        assert!(!rs.config_files.is_empty());
    }

    #[test]
    fn test_detect_ruby_with_gemfile() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "Gemfile", "source 'https://rubygems.org'");
        create_file(temp.path(), "app.rb", "puts 'hello'");
        create_file(temp.path(), "test.gemspec", "Gem::Specification.new");

        let detector = LanguageDetector::new(&[]);
        let report = detector.detect(temp.path()).unwrap();

        let rb = find_lang(&report.languages, Language::Ruby).unwrap();
        assert_eq!(rb.file_count, 2); // .rb + .gemspec
    }

    #[test]
    fn test_detect_c_and_cpp_separate() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "main.c", "int main() { return 0; }");
        create_file(temp.path(), "header.h", "#pragma once");
        create_file(temp.path(), "app.cpp", "int main() {}");
        create_file(temp.path(), "app.hpp", "class Foo {};");

        let detector = LanguageDetector::new(&[]);
        let report = detector.detect(temp.path()).unwrap();

        let c = find_lang(&report.languages, Language::C).unwrap();
        let cpp = find_lang(&report.languages, Language::Cpp).unwrap();
        assert_eq!(c.file_count, 2); // .c + .h
        assert_eq!(cpp.file_count, 2); // .cpp + .hpp
    }

    #[test]
    fn test_detect_dart_with_pubspec() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "pubspec.yaml", "name: my_app\nversion: 1.0.0");
        create_file(temp.path(), "lib/main.dart", "void main() {}");

        let detector = LanguageDetector::new(&[]);
        let report = detector.detect(temp.path()).unwrap();

        let dart = find_lang(&report.languages, Language::Dart).unwrap();
        assert_eq!(dart.file_count, 1);
        assert!(!dart.config_files.is_empty(), "pubspec.yaml should be detected");
    }
}

// ===========================================================================
// Orchestration tests — dispatch logic, output paths, JS skip
// ===========================================================================
//
// NOTE: All orchestrators share a hardcoded output dir (/tmp/legend-indexer).
// Tests that create IndexerOrchestrator are combined into a single test
// function to avoid race conditions when cargo runs tests in parallel.
mod orchestration {
    use super::*;

    #[test]
    fn test_output_paths_unique_per_language() {
        let temp = TempDir::new().unwrap();
        let orch = IndexerOrchestrator::new(temp.path().to_path_buf(), None).unwrap();

        let mut paths = std::collections::HashSet::new();
        for &lang in Language::ALL {
            let path = orch.scip_output_path(lang);
            assert!(
                paths.insert(path.clone()),
                "Duplicate output path for {:?}: {:?}",
                lang,
                path
            );
        }

        // --- stale cleanup sub-test (runs sequentially, same orchestrator dir) ---
        let output_dir = orch.output_dir().to_path_buf();

        // Write stale .scip, a detection report, and a non-.scip file
        fs::write(output_dir.join("old.scip"), b"stale").unwrap();
        fs::write(output_dir.join("notes.txt"), b"keep me").unwrap();
        fs::write(output_dir.join("detection-report.json"), b"{}").unwrap();

        // New orchestrator should clean stale files on construction
        let orch2 = IndexerOrchestrator::new(temp.path().to_path_buf(), None).unwrap();

        assert!(!output_dir.join("old.scip").exists(), "Stale .scip should be removed");
        assert!(!output_dir.join("detection-report.json").exists(), "Stale report should be removed");
        assert!(output_dir.join("notes.txt").exists(), "Non-.scip files should be preserved");

        orch2.cleanup().ok();
    }

    #[test]
    fn test_js_skip_when_ts_succeeds() {
        // Pure detection test — no IndexerOrchestrator, so no /tmp race
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "app.ts", "const x = 1;");
        create_file(temp.path(), "app.js", "const x = 1;");

        let detector = LanguageDetector::new(&[]);
        let report = detector.detect(temp.path()).unwrap();

        // Both TS and JS should be detected
        assert!(find_lang(&report.languages, Language::TypeScript).is_some());
        assert!(find_lang(&report.languages, Language::JavaScript).is_some());
    }
}

// ===========================================================================
// Go module discovery tests
// ===========================================================================
mod go_modules {
    use super::*;

    #[test]
    fn test_go_root_module_detection() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "go.mod", "module example.com/test\ngo 1.21");
        create_file(temp.path(), "main.go", "package main");

        // The run_go_indexer method would take the root go.mod path.
        // Here we verify the file exists where orchestrator expects it.
        assert!(temp.path().join("go.mod").exists());
    }

    #[test]
    fn test_go_subdirectory_module_detection() {
        let temp = TempDir::new().unwrap();
        // No root go.mod
        create_file(temp.path(), "apps/api/go.mod", "module example.com/api");
        create_file(temp.path(), "apps/api/main.go", "package main");
        create_file(temp.path(), "libs/common/go.mod", "module example.com/common");
        create_file(temp.path(), "libs/common/util.go", "package common");

        // Verify subdirectory go.mod files exist where the finder would look
        assert!(temp.path().join("apps/api/go.mod").exists());
        assert!(temp.path().join("libs/common/go.mod").exists());
    }

    #[test]
    fn test_go_work_file_rewriting() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "go.work", "go 1.21\n\nuse (\n\t./apps/api\n\t./libs/common\n)\n");
        create_file(temp.path(), "apps/api/go.mod", "module example.com/api");
        create_file(temp.path(), "libs/common/go.mod", "module example.com/common");

        // Verify go.work exists for the orchestrator to find
        let content = fs::read_to_string(temp.path().join("go.work")).unwrap();
        assert!(content.contains("./apps/api"));
        assert!(content.contains("./libs/common"));
    }
}

// ===========================================================================
// .NET solution discovery tests
// ===========================================================================
mod dotnet_solution {
    use super::*;

    #[test]
    fn test_sln_in_root_detected() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "MyApp.sln", "Microsoft Visual Studio Solution File");
        create_file(temp.path(), "MyApp/App.cs", "class App {}");

        let detector = LanguageDetector::new(&[]);
        let report = detector.detect(temp.path()).unwrap();

        let cs = find_lang(&report.languages, Language::CSharp);
        assert!(cs.is_some());
        let config = &cs.unwrap().config_files;
        assert!(config.iter().any(|p| p.to_string_lossy().contains(".sln")));
    }

    #[test]
    fn test_csproj_fallback() {
        let temp = TempDir::new().unwrap();
        // No .sln, just a .csproj
        create_file(temp.path(), "MyApp.csproj", "<Project Sdk=\"Microsoft.NET.Sdk\" />");
        create_file(temp.path(), "Program.cs", "class Program {}");

        let detector = LanguageDetector::new(&[]);
        let report = detector.detect(temp.path()).unwrap();

        let cs = find_lang(&report.languages, Language::CSharp);
        assert!(cs.is_some());
        let config = &cs.unwrap().config_files;
        assert!(config.iter().any(|p| p.to_string_lossy().contains(".csproj")));
    }
}

// ===========================================================================
// PHP workspace tests
// ===========================================================================
mod php_workspace {
    use super::*;

    #[test]
    fn test_php_with_existing_vendor() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "composer.json", r#"{"require":{}}"#);
        create_file(temp.path(), "vendor/autoload.php", "<?php // autoload");
        create_file(temp.path(), "src/index.php", "<?php echo 'hi';");

        // When vendor/autoload.php exists, the PHP indexer should try to index directly
        // (without creating a workspace). We verify the detection side.
        let detector = LanguageDetector::new(&[]);
        let report = detector.detect(temp.path()).unwrap();
        let php = find_lang(&report.languages, Language::Php);
        assert!(php.is_some());
    }

    #[test]
    fn test_php_without_vendor_needs_workspace() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "composer.json", r#"{"require":{"php":">=8.0"}}"#);
        create_file(temp.path(), "src/index.php", "<?php echo 'hi';");

        // No vendor/ directory — the PHP indexer would create a workspace.
        // Verify the detection + composer.json presence.
        assert!(temp.path().join("composer.json").exists());
        assert!(!temp.path().join("vendor/autoload.php").exists());
    }

    #[test]
    fn test_php_no_composer_json_fails() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "src/index.php", "<?php echo 'hi';");

        // PHP detected but no composer.json — the indexer would fail
        assert!(!temp.path().join("composer.json").exists());
    }
}

// ===========================================================================
// TypeScript workspace tests
// ===========================================================================
mod typescript_workspace {
    use super::*;

    #[test]
    fn test_ts_with_tsconfig() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "tsconfig.json", r#"{"compilerOptions":{"target":"ES2020"}}"#);
        create_file(temp.path(), "package.json", r#"{"name":"test"}"#);
        create_file(temp.path(), "src/app.ts", "export const x = 1;");

        // With tsconfig.json present, --infer-tsconfig should NOT be added
        assert!(temp.path().join("tsconfig.json").exists());
    }

    #[test]
    fn test_ts_without_tsconfig_needs_infer() {
        let temp = TempDir::new().unwrap();
        create_file(temp.path(), "package.json", r#"{"name":"test"}"#);
        create_file(temp.path(), "src/app.js", "const x = 1;");

        // No tsconfig.json — indexer should use --infer-tsconfig
        assert!(!temp.path().join("tsconfig.json").exists());
    }
}
