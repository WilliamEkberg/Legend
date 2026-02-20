//! SCIP Engine - Universal SCIP Indexer Runner
//!
//! This library provides language-agnostic SCIP index generation
//! using Sourcegraph SCIP indexers. It detects languages, orchestrates
//! indexer execution, and produces raw .scip protobuf files.

pub mod config;
pub mod detect;
pub mod orchestrate;

pub use config::Config;
pub use detect::{DetectionReport, LanguageDetector};
pub use orchestrate::IndexerOrchestrator;
