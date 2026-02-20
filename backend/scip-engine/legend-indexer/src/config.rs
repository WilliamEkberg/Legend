//! Configuration handling for scip-engine

use std::path::PathBuf;

/// Configuration for the indexer
#[derive(Debug, Clone)]
pub struct Config {
    /// Path to the codebase to analyze
    pub input_path: PathBuf,

    /// Output directory for .scip files (None = leave in .legend-indexer/)
    pub output_path: Option<PathBuf>,

    /// Languages to analyze (empty means auto-detect all)
    pub languages: Vec<String>,

    /// Glob patterns to exclude
    pub exclude_patterns: Vec<String>,

    /// Path to bundled indexers
    pub indexers_path: Option<PathBuf>,

    /// Verbosity level
    pub verbose: bool,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            input_path: PathBuf::from("."),
            output_path: None,
            languages: Vec::new(),
            exclude_patterns: vec![
                "node_modules/**".to_string(),
                ".git/**".to_string(),
                "target/**".to_string(),
                "dist/**".to_string(),
                "build/**".to_string(),
                "__pycache__/**".to_string(),
                ".venv/**".to_string(),
                "venv/**".to_string(),
                ".env/**".to_string(),
                "env/**".to_string(),
                "*.min.js".to_string(),
                "*.min.css".to_string(),
            ],
            indexers_path: None,
            verbose: false,
        }
    }
}
