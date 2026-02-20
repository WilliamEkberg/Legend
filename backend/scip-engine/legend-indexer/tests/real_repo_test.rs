//! Real-repo integration tests for scip-engine
//!
//! Runs detection against real cloned GitHub repositories to validate
//! bug fixes and robustness on production-scale codebases.
//!
//! These tests require repos to be cloned at known paths. They are skipped
//! automatically if the repos are not present.

mod common;

use common::skip_unless;
use legend_indexer::config::Config;
use legend_indexer::detect::{Language, LanguageDetector};
use std::path::Path;

const SUPABASE_PATH: &str = "/test-repos/supabase";
const OLLAMA_PATH: &str = "/test-repos/ollama";
const ZED_PATH: &str = "/test-repos/zed";

// ---------------------------------------------------------------------------
// Bug 4 validation: nested node_modules exclusion on a REAL monorepo
// ---------------------------------------------------------------------------
#[test]
fn test_supabase_nested_node_modules_excluded() {
    skip_unless!(SUPABASE_PATH);

    let detector = LanguageDetector::new(&Config::default().exclude_patterns);

    let detected = detector.detect(Path::new(SUPABASE_PATH)).unwrap().languages;

    let ts = detected.iter().find(|i| i.language == Language::TypeScript);
    assert!(ts.is_some(), "Supabase should have TypeScript files");

    let ts_count = ts.unwrap().file_count;
    println!("Supabase TypeScript files (excluding node_modules): {}", ts_count);
    assert!(
        ts_count < 15000,
        "Bug 4 regression: got {} TS files — node_modules likely leaking through!",
        ts_count
    );
    assert!(
        ts_count > 100,
        "Too few TS files ({}), something is wrong with detection",
        ts_count
    );
}

// ---------------------------------------------------------------------------
// Detection correctness on real repos
// ---------------------------------------------------------------------------
#[test]
fn test_ollama_detects_go_as_primary() {
    skip_unless!(OLLAMA_PATH);

    let detector = LanguageDetector::new(&[
        ".git/**".to_string(),
        "node_modules/**".to_string(),
    ]);
    let detected = detector.detect(Path::new(OLLAMA_PATH)).unwrap().languages;

    assert!(!detected.is_empty(), "Ollama should detect languages");

    let primary = &detected[0];
    assert_eq!(
        primary.language,
        Language::Go,
        "Ollama's primary language should be Go, got {:?}",
        primary.language
    );
    println!("Ollama primary: Go with {} files", primary.file_count);

    let langs: Vec<Language> = detected.iter().map(|i| i.language).collect();
    assert!(langs.contains(&Language::Cpp), "Ollama should have C++ files");
    assert!(langs.contains(&Language::C), "Ollama should have C files");

    for info in &detected {
        println!("  {:?}: {} files", info.language, info.file_count);
    }
}

#[test]
fn test_zed_detects_rust_as_primary() {
    skip_unless!(ZED_PATH);

    let detector = LanguageDetector::new(&[
        ".git/**".to_string(),
        "target/**".to_string(),
        "node_modules/**".to_string(),
    ]);
    let detected = detector.detect(Path::new(ZED_PATH)).unwrap().languages;

    assert!(!detected.is_empty(), "Zed should detect languages");

    let primary = &detected[0];
    assert_eq!(
        primary.language,
        Language::Rust,
        "Zed's primary language should be Rust, got {:?}",
        primary.language
    );
    println!("Zed primary: Rust with {} files", primary.file_count);
    assert!(
        primary.file_count > 1000,
        "Zed should have 1000+ Rust files, got {}",
        primary.file_count
    );
}

#[test]
fn test_supabase_detects_typescript_as_primary() {
    skip_unless!(SUPABASE_PATH);

    let detector = LanguageDetector::new(&[
        ".git/**".to_string(),
        "node_modules/**".to_string(),
        "dist/**".to_string(),
        "build/**".to_string(),
    ]);
    let detected = detector.detect(Path::new(SUPABASE_PATH)).unwrap().languages;

    assert!(!detected.is_empty());

    let primary = &detected[0];
    assert_eq!(
        primary.language,
        Language::TypeScript,
        "Supabase's primary language should be TypeScript, got {:?}",
        primary.language
    );
    println!("Supabase primary: TypeScript with {} files", primary.file_count);
}

// ---------------------------------------------------------------------------
// Detection determinism on real repos (run twice, compare)
// ---------------------------------------------------------------------------
#[test]
fn test_supabase_detection_determinism() {
    skip_unless!(SUPABASE_PATH);

    let root = Path::new(SUPABASE_PATH);
    let detector = LanguageDetector::new(&[
        "node_modules/**".to_string(),
        ".git/**".to_string(),
    ]);

    let run1 = detector.detect(root).unwrap().languages;
    let run2 = detector.detect(root).unwrap().languages;

    let mut snap1: Vec<(String, usize)> = run1
        .iter()
        .map(|i| (i.language.display_name().to_string(), i.file_count))
        .collect();
    let mut snap2: Vec<(String, usize)> = run2
        .iter()
        .map(|i| (i.language.display_name().to_string(), i.file_count))
        .collect();

    snap1.sort();
    snap2.sort();

    assert_eq!(snap1, snap2, "Supabase detection should be deterministic");
    println!("Supabase determinism check passed: {:?}", snap1);
}

// ---------------------------------------------------------------------------
// Performance sanity: detection should complete in reasonable time
// ---------------------------------------------------------------------------
#[test]
fn test_large_repo_detection_under_5_seconds() {
    skip_unless!(SUPABASE_PATH);

    let root = Path::new(SUPABASE_PATH);
    let detector = LanguageDetector::new(&[
        "node_modules/**".to_string(),
        ".git/**".to_string(),
    ]);

    let start = std::time::Instant::now();
    let detected = detector.detect(root).unwrap().languages;
    let elapsed = start.elapsed();

    println!(
        "Supabase (14k files) detection took: {:.2}s, found {} languages",
        elapsed.as_secs_f64(),
        detected.len()
    );

    assert!(
        elapsed.as_secs() < 5,
        "Detection took too long: {:.2}s (should be < 5s)",
        elapsed.as_secs_f64()
    );
}
