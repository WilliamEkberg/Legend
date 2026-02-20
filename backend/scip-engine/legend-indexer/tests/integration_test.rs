//! Integration tests for legend-indexer

mod common;

use common::create_file;
use assert_cmd::Command;
use predicates::prelude::*;
use std::fs;
use tempfile::TempDir;

fn create_typescript_project(dir: &TempDir) {
    create_file(dir.path(), "package.json", r#"{"name":"test","version":"1.0.0"}"#);
    create_file(dir.path(), "tsconfig.json", r#"{"compilerOptions":{"target":"ES2020"}}"#);
    create_file(dir.path(), "src/utils/format.ts", "export function formatDate(d: Date): string { return d.toISOString(); }");
    create_file(dir.path(), "src/components/Display.tsx", "import { formatDate } from '../utils/format';\nexport function Display() { return <div/>; }");
    create_file(dir.path(), "src/index.ts", "export { Display } from './components/Display';");
}

#[test]
fn test_cli_help() {
    Command::cargo_bin("legend-indexer")
        .unwrap()
        .arg("--help")
        .assert()
        .success()
        .stdout(predicate::str::contains("Universal SCIP indexer runner"));
}

#[test]
fn test_cli_version() {
    Command::cargo_bin("legend-indexer")
        .unwrap()
        .arg("--version")
        .assert()
        .success()
        .stdout(predicate::str::contains("scip-engine"));
}

#[test]
fn test_detect_empty_directory() {
    let temp_dir = TempDir::new().unwrap();

    Command::cargo_bin("legend-indexer")
        .unwrap()
        .arg("detect")
        .arg(temp_dir.path())
        .assert()
        .success()
        .stdout(predicate::str::contains("No supported programming languages"));
}

#[test]
fn test_check_indexers() {
    Command::cargo_bin("legend-indexer")
        .unwrap()
        .arg("check-indexers")
        .assert()
        .success()
        .stdout(predicate::str::contains("SCIP Indexer Availability"))
        .stdout(predicate::str::contains("TypeScript"))
        .stdout(predicate::str::contains("Python"));
}

#[test]
fn test_language_filter() {
    let temp_dir = TempDir::new().unwrap();
    create_typescript_project(&temp_dir);

    fs::write(temp_dir.path().join("script.py"), "print('hello')").unwrap();

    Command::cargo_bin("legend-indexer")
        .unwrap()
        .arg("detect")
        .arg(temp_dir.path())
        .assert()
        .success()
        .stdout(predicate::str::contains("TypeScript"))
        .stdout(predicate::str::contains("Python"));
}

#[test]
fn test_exclude_patterns() {
    let temp_dir = TempDir::new().unwrap();
    create_typescript_project(&temp_dir);

    create_file(temp_dir.path(), "node_modules/dep/index.ts", "export const x = 1;");

    Command::cargo_bin("legend-indexer")
        .unwrap()
        .arg("detect")
        .arg(temp_dir.path())
        .assert()
        .success();
}
