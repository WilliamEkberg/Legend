//! SCIP Indexer Orchestration
//!
//! Manages the execution of SCIP indexers for different languages.

use crate::detect::{Language, LanguageInfo};
use anyhow::{anyhow, Context, Result};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::Command;
use tracing::{debug, info, warn};

/// Result of running an indexer
#[derive(Debug)]
pub struct IndexerResult {
    pub language: Language,
    pub scip_path: PathBuf,
    pub success: bool,
    pub error: Option<String>,
}

/// Detected Node.js version hint from a repository
struct NodeVersionHint {
    source: &'static str,
    raw: String,
    major: u32,
}

/// Check whether a directory contains any TS/JS source files (max depth 5).
fn has_ts_js_files(dir: &Path) -> bool {
    for entry in walkdir::WalkDir::new(dir)
        .max_depth(5)
        .into_iter()
        .filter_entry(|e| {
            let name = e.file_name().to_string_lossy();
            name != "node_modules" && name != "dist" && name != ".next"
        })
        .filter_map(|e| e.ok())
    {
        if entry.file_type().is_file() {
            if let Some(ext) = entry.path().extension().and_then(|e| e.to_str()) {
                match ext {
                    "ts" | "tsx" | "js" | "jsx" | "mts" | "cts" => return true,
                    _ => {}
                }
            }
        }
    }
    false
}

/// Orchestrates SCIP indexer execution
pub struct IndexerOrchestrator {
    indexers_path: Option<PathBuf>,
    codebase_path: PathBuf,
    output_dir: PathBuf,
}

impl IndexerOrchestrator {
    /// Create a new orchestrator
    pub fn new(codebase_path: PathBuf, indexers_path: Option<PathBuf>) -> Result<Self> {
        // Write temp SCIP files to /tmp, NOT the codebase directory.
        // This allows the codebase to be mounted read-only in Docker.
        let output_dir = PathBuf::from("/tmp/legend-indexer");

        // Remove stale .scip files and detection report from previous runs.
        // Ignore NotFound errors — the file (or directory) may have been removed
        // between enumeration and deletion (harmless race, especially in tests).
        if output_dir.exists() {
            if let Ok(entries) = std::fs::read_dir(&output_dir) {
                for entry in entries.flatten() {
                    let path = entry.path();
                    let is_stale = path.extension().is_some_and(|ext| ext == "scip")
                        || path.file_name().is_some_and(|n| n == "detection-report.json");
                    if is_stale {
                        debug!("Removing stale file: {:?}", path);
                        if let Err(e) = std::fs::remove_file(&path) {
                            if e.kind() != std::io::ErrorKind::NotFound {
                                return Err(anyhow::anyhow!("Failed to remove stale file {:?}: {}", path, e));
                            }
                        }
                    }
                }
            }
        }

        std::fs::create_dir_all(&output_dir)
            .context("Failed to create output directory")?;

        Ok(Self {
            indexers_path,
            codebase_path,
            output_dir,
        })
    }

    /// Check if an indexer is available (either bundled or in PATH)
    pub fn is_indexer_available(&self, language: Language) -> bool {
        self.get_bundled_path(language.scip_indexer()).is_some()
            || self.find_indexer_in_path(language).is_some()
    }

    /// Find an indexer binary in PATH
    fn find_indexer_in_path(&self, language: Language) -> Option<PathBuf> {
        for name in language.scip_binary_names() {
            if let Ok(path) = which::which(name) {
                return Some(path);
            }
        }

        // For languages that use npm/node packages, check npx availability
        if matches!(language, Language::TypeScript | Language::JavaScript)
            && which::which("npx").is_ok()
        {
            return Some(PathBuf::from("npx"));
        }

        // Dart is invoked via `dart pub global run scip_dart`, so check for `dart`
        if language == Language::Dart && which::which("dart").is_ok() {
            return Some(PathBuf::from("dart"));
        }

        None
    }

    /// Run the appropriate indexer for a language
    pub fn run_indexer(&self, language: Language) -> Result<IndexerResult> {
        info!("Running indexer for {:?}", language);

        let scip_output = self.scip_output_path(language);
        let output_str = scip_output.to_str().unwrap();

        let result = match language {
            Language::TypeScript | Language::JavaScript => {
                self.run_typescript_indexer(&scip_output)
            }
            Language::Python => self.run_python_indexer(&scip_output),
            Language::CSharp => self.run_dotnet_indexer(&scip_output),
            Language::Java | Language::Kotlin | Language::Scala => {
                self.run_java_indexer(&scip_output)
            }
            Language::Go => self.run_go_indexer(&scip_output),
            Language::Rust => self.run_simple_indexer("rust-analyzer", &["scip", ".", "--output", output_str]),
            Language::Ruby => self.run_simple_indexer("scip-ruby", &["--index-file", output_str]),
            Language::Php => self.run_php_indexer(&scip_output),
            Language::Cpp | Language::C => self.run_clang_indexer(&scip_output),
            Language::Dart => self.run_dart_indexer(&scip_output),
        };

        match result {
            Ok(()) => Ok(IndexerResult {
                language,
                scip_path: scip_output,
                success: true,
                error: None,
            }),
            Err(e) => {
                warn!("Indexer failed for {:?}: {}", language, e);
                Ok(IndexerResult {
                    language,
                    scip_path: scip_output,
                    success: false,
                    error: Some(e.to_string()),
                })
            }
        }
    }

    /// Run indexers for all detected languages
    pub fn run_all(&self, languages: &[LanguageInfo]) -> Vec<IndexerResult> {
        let mut results = Vec::new();
        let mut ts_succeeded = false;

        for lang_info in languages {
            // scip-typescript indexes both .ts and .js files in a single run.
            // Skip JavaScript if TypeScript already succeeded to avoid redundant
            // indexing that wastes time and can OOM on large monorepos.
            if lang_info.language == Language::JavaScript && ts_succeeded {
                info!("Skipping JavaScript indexer — already covered by TypeScript run");
                continue;
            }

            if !self.is_indexer_available(lang_info.language) {
                warn!(
                    "Indexer for {:?} not available. Install with: {}",
                    lang_info.language,
                    lang_info.language.install_command()
                );
                results.push(IndexerResult {
                    language: lang_info.language,
                    scip_path: PathBuf::new(),
                    success: false,
                    error: Some("Indexer not installed".to_string()),
                });
                continue;
            }

            match self.run_indexer(lang_info.language) {
                Ok(result) => {
                    if result.success && lang_info.language == Language::TypeScript {
                        ts_succeeded = true;
                    }
                    results.push(result);
                }
                Err(e) => {
                    results.push(IndexerResult {
                        language: lang_info.language,
                        scip_path: PathBuf::new(),
                        success: false,
                        error: Some(e.to_string()),
                    });
                }
            }
        }

        results
    }

    /// Run scip-typescript indexer in a writable workspace.
    ///
    /// One unified path for all repos: deep-copy the codebase, install deps,
    /// ensure tsconfigs exist, detect workspace flags, and run scip-typescript.
    fn run_typescript_indexer(&self, output: &Path) -> Result<()> {
        self.ensure_node_version(); // Non-fatal, switches Node if needed

        let output_str = output.to_str().unwrap();

        // Always deep-copy into a writable workspace so scip-typescript never
        // writes to the user's codebase and all source files are available.
        info!("Creating writable TypeScript workspace (deep copy)");
        let workspace = self.create_ts_workspace()?;

        // Always use --yarn-workspaces for monorepos. We bundle Yarn 1 and control it.
        // --pnpm-workspaces calls `pnpm ls -r` internally which fails on PM version
        // mismatches (pnpm 9 vs 10, engine constraints, etc.). By normalizing all
        // workspace configs into package.json "workspaces" (done in create_ts_workspace),
        // Yarn 1's workspace discovery works for any repo type.
        let workspace_flag = if self.has_workspaces_field(&workspace) && which::which("yarn").is_ok() {
            Some("--yarn-workspaces")
        } else {
            None
        };

        if let Some(flag) = workspace_flag {
            info!("Detected monorepo workspace ({})", flag);
        }

        let mut args = vec!["index", "--output", output_str, "--max-file-byte-size", "10mb"];
        if let Some(flag) = workspace_flag {
            args.push(flag);
        }
        // For non-workspace projects, use --infer-tsconfig so scip-typescript
        // discovers TS files even without an explicit tsconfig.json include.
        if workspace_flag.is_none() && !workspace.join("tsconfig.json").exists() {
            args.push("--infer-tsconfig");
        }

        self.try_typescript_from_dir(&args, &workspace)
    }

    /// Detect the required Node.js version from the target repository.
    /// Checks version hints in priority order:
    /// 1. .node-version file (explicit single version)
    /// 2. .nvmrc file (handles lts/* → use default)
    /// 3. package.json engines.node field (extract minimum major)
    /// 4. package.json volta.node field
    /// 5. No hint → None (use container default)
    fn detect_required_node_version(&self) -> Option<NodeVersionHint> {
        // 1. .node-version file (highest priority — explicit project pin)
        let node_version_file = self.codebase_path.join(".node-version");
        if node_version_file.exists() {
            if let Ok(raw) = std::fs::read_to_string(&node_version_file) {
                let raw = raw.trim().to_string();
                if let Some(major) = Self::extract_major_version(&raw) {
                    return Some(NodeVersionHint { source: ".node-version", raw, major });
                }
            }
        }

        // 2. .nvmrc file
        let nvmrc = self.codebase_path.join(".nvmrc");
        if nvmrc.exists() {
            if let Ok(raw) = std::fs::read_to_string(&nvmrc) {
                let raw = raw.trim().to_string();
                // lts/* or lts/hydrogen etc. → use default (we already have LTS)
                if raw.starts_with("lts") {
                    return None;
                }
                if let Some(major) = Self::extract_major_version(&raw) {
                    return Some(NodeVersionHint { source: ".nvmrc", raw, major });
                }
            }
        }

        // 3 & 4. package.json engines.node / volta.node
        let pkg_path = self.codebase_path.join("package.json");
        if pkg_path.exists() {
            if let Ok(contents) = std::fs::read_to_string(&pkg_path) {
                if let Ok(pkg) = serde_json::from_str::<serde_json::Value>(&contents) {
                    // 3. engines.node (e.g. ">=22", "^22.0.0", "22")
                    if let Some(engines_node) = pkg.get("engines")
                        .and_then(|e| e.get("node"))
                        .and_then(|n| n.as_str())
                    {
                        let raw = engines_node.to_string();
                        if let Some(major) = Self::extract_major_version(&raw) {
                            return Some(NodeVersionHint { source: "package.json engines.node", raw, major });
                        }
                    }

                    // 4. volta.node (e.g. "22.0.0")
                    if let Some(volta_node) = pkg.get("volta")
                        .and_then(|v| v.get("node"))
                        .and_then(|n| n.as_str())
                    {
                        let raw = volta_node.to_string();
                        if let Some(major) = Self::extract_major_version(&raw) {
                            return Some(NodeVersionHint { source: "package.json volta.node", raw, major });
                        }
                    }
                }
            }
        }

        None
    }

    /// Extract the leading major version number from a version string (handles >=, ^, ~, v prefixes).
    fn extract_major_version(s: &str) -> Option<u32> {
        // Skip leading non-digit characters (>=, ^, ~, v, etc.)
        let digits_start = s.find(|c: char| c.is_ascii_digit())?;
        let rest = &s[digits_start..];
        // Take consecutive digits
        let end = rest.find(|c: char| !c.is_ascii_digit()).unwrap_or(rest.len());
        rest[..end].parse().ok()
    }

    /// Switch Node.js version if the target repo requires a different one.
    /// Uses `n` version manager which swaps /usr/local/bin/node in-place.
    /// Non-fatal on failure — logs warning and continues with current version.
    fn ensure_node_version(&self) {
        let hint = match self.detect_required_node_version() {
            Some(h) => h,
            None => return,
        };

        info!(
            "Detected Node.js version requirement: {} (from {}, raw: {:?})",
            hint.major, hint.source, hint.raw
        );

        // Get current Node.js major version
        let current_major = match Command::new("node")
            .arg("--version")
            .output()
        {
            Ok(output) if output.status.success() => {
                let version_str = String::from_utf8_lossy(&output.stdout);
                Self::extract_major_version(version_str.trim())
            }
            _ => {
                warn!("Could not determine current Node.js version");
                return;
            }
        };

        let current = match current_major {
            Some(v) => v,
            None => return,
        };

        // Determine whether the constraint is an exact pin or a range.
        // Exact pins (.node-version, volta.node): switch if current != target.
        // Range constraints (package.json engines.node, .nvmrc): keep current if current >= target.
        let is_range = matches!(
            hint.source,
            "package.json engines.node" | ".nvmrc"
        );

        if is_range {
            if current >= hint.major {
                info!(
                    "Node.js v{} already satisfies range constraint {} (from {})",
                    current, hint.raw, hint.source
                );
                return;
            }
        } else if current == hint.major {
            info!("Node.js v{} already matches requirement", current);
            return;
        }

        info!("Switching Node.js from v{} to v{} via `n`", current, hint.major);

        match Command::new("n")
            .arg(hint.major.to_string())
            .status()
        {
            Ok(status) if status.success() => {
                info!("Successfully switched to Node.js v{}", hint.major);
            }
            Ok(status) => {
                warn!(
                    "Failed to switch Node.js to v{} (exit {:?}), continuing with v{}",
                    hint.major,
                    status.code(),
                    current
                );
            }
            Err(e) => {
                warn!(
                    "Failed to run `n {}`: {}, continuing with Node.js v{}",
                    hint.major, e, current
                );
            }
        }
    }

    /// Check if package.json in the given directory has a "workspaces" field
    fn has_workspaces_field(&self, dir: &Path) -> bool {
        let pkg_path = dir.join("package.json");
        if !pkg_path.exists() {
            return false;
        }
        std::fs::read_to_string(&pkg_path)
            .ok()
            .and_then(|contents| serde_json::from_str::<serde_json::Value>(&contents).ok())
            .map(|v| v.get("workspaces").is_some())
            .unwrap_or(false)
    }

    /// Create a writable workspace for scip-typescript by deep-copying the codebase.
    /// Works for any repo: single project, monorepo, NX, etc.
    ///
    /// Steps:
    /// 1. Deep-copy via tar (excludes .git, node_modules, build artifacts)
    /// 2. Synthesize workspaces field for NX monorepos (project.json discovery)
    /// 3. Generate package.json for NX workspace dirs missing one (yarn visibility)
    /// 4. Install node dependencies (yarn/pnpm/npm — detected from lock file)
    /// 5. Generate missing tsconfig.json for workspace packages
    fn create_ts_workspace(&self) -> Result<PathBuf> {
        let ws = self.output_dir.join("ts-workspace");
        if ws.exists() {
            std::fs::remove_dir_all(&ws)?;
        }
        std::fs::create_dir_all(&ws)?;

        info!("Deep-copying codebase to writable workspace via tar...");
        let mut tar_child = Command::new("tar")
            .current_dir(&self.codebase_path)
            .args([
                "-cf", "-",
                "--exclude=.git", "--exclude=node_modules",
                "--exclude=dist", "--exclude=build",
                "--exclude=.next", "--exclude=coverage",
                "--exclude=__pycache__", ".",
            ])
            .stdout(std::process::Stdio::piped())
            .spawn()?;

        let extract_status = Command::new("tar")
            .current_dir(&ws)
            .args(["-xf", "-"])
            .stdin(tar_child.stdout.take().unwrap())
            .status()?;

        tar_child.wait()?;

        if !extract_status.success() {
            return Err(anyhow!("tar copy to ts-workspace failed"));
        }

        self.sanitize_package_json(&ws);
        self.ensure_pnpm_workspaces(&ws);
        self.ensure_nx_workspaces(&ws);
        self.ensure_nx_package_jsons(&ws);

        self.ensure_all_tsconfig_dirs_in_workspace(&ws);

        // Install deps for tsconfig/import resolution
        self.install_node_dependencies(&ws)?;

        // Generate tsconfig.json for workspace packages that lack one.
        // scip-typescript silently skips packages without tsconfig.json.
        self.ensure_workspace_tsconfigs(&ws);

        info!("Created writable ts-workspace at {:?}", ws);
        Ok(ws)
    }

    /// Detect the package manager from lock files and run install with --ignore-scripts.
    /// We only need the node_modules structure for import/tsconfig resolution, not build
    /// artifacts, so we intentionally avoid --frozen-lockfile / npm ci — lockfile formats
    /// change across PM versions and we have no guarantee the bundled PM version matches
    /// what the project was developed with.
    /// Non-fatal on failure (matches patterns in try_install_python_deps and download_go_deps).
    fn install_node_dependencies(&self, workspace: &Path) -> Result<()> {
        // Delete lockfiles that our bundled PM version can't parse. Yarn Berry (v2+)
        // and pnpm v9 use different lockfile formats than Yarn 1 / pnpm v7.
        // Without a lockfile the PM resolves from the registry (slower but always works).
        self.sanitize_lockfiles(workspace);

        let (cmd, args): (&str, Vec<&str>) =
            if workspace.join("pnpm-lock.yaml").exists() && which::which("pnpm").is_ok() {
                info!("Running pnpm install in workspace...");
                ("pnpm", vec!["install", "--ignore-scripts", "--config.engine-strict=false"])
            } else if workspace.join("yarn.lock").exists() && which::which("yarn").is_ok() {
                info!("Running yarn install in workspace...");
                ("yarn", vec!["install", "--ignore-scripts", "--ignore-engines"])
            } else if workspace.join("package-lock.json").exists() {
                info!("Running npm install in workspace...");
                ("npm", vec!["install", "--ignore-scripts"])
            } else if workspace.join("package.json").exists() {
                info!("Running npm install in workspace (no lockfile)...");
                ("npm", vec!["install", "--ignore-scripts"])
            } else {
                warn!("No package.json found, skipping dependency install");
                return Ok(());
            };

        let status = Command::new(cmd)
            .current_dir(workspace)
            .args(&args)
            .status()?;

        if status.success() {
            info!("{} install completed", cmd);
        } else {
            warn!("{} install failed (exit {:?}), proceeding anyway", cmd, status.code());
        }

        Ok(())
    }

    /// Generate tsconfig.json for workspace packages missing one, so scip-typescript
    /// doesn't silently skip them. Non-fatal on errors.
    fn ensure_workspace_tsconfigs(&self, workspace: &Path) {
        let pkg_path = workspace.join("package.json");
        let pkg_contents = match std::fs::read_to_string(&pkg_path) {
            Ok(c) => c,
            Err(_) => return,
        };
        let pkg: serde_json::Value = match serde_json::from_str(&pkg_contents) {
            Ok(v) => v,
            Err(_) => return,
        };

        // Supports both array and { "packages": [...] } (yarn berry) formats
        let workspace_globs: Vec<String> = match pkg.get("workspaces") {
            Some(serde_json::Value::Array(arr)) => {
                arr.iter().filter_map(|v| v.as_str().map(String::from)).collect()
            }
            Some(serde_json::Value::Object(obj)) => {
                // yarn berry format: { "packages": ["packages/*", ...] }
                obj.get("packages")
                    .and_then(|v| v.as_array())
                    .map(|arr| arr.iter().filter_map(|v| v.as_str().map(String::from)).collect())
                    .unwrap_or_default()
            }
            _ => return,
        };

        if workspace_globs.is_empty() {
            return;
        }

        let has_root_tsconfig = workspace.join("tsconfig.json").exists();
        let mut generated_count = 0u32;

        for pattern in &workspace_globs {
            let full_pattern = format!("{}/{}", workspace.display(), pattern);
            let matches = match glob::glob(&full_pattern) {
                Ok(paths) => paths,
                Err(e) => {
                    warn!("Invalid workspace glob pattern {:?}: {}", pattern, e);
                    continue;
                }
            };

            for entry in matches.flatten() {
                if !entry.is_dir() {
                    continue;
                }
                // Only process directories that have package.json but no tsconfig.json
                if !entry.join("package.json").exists() || entry.join("tsconfig.json").exists() {
                    continue;
                }

                let tsconfig_content = if has_root_tsconfig {
                    // Compute relative path back to root tsconfig
                    let rel_to_root = pathdiff::diff_paths(workspace, &entry)
                        .unwrap_or_else(|| PathBuf::from("../.."));
                    let extends_path = format!("{}/tsconfig.json", rel_to_root.display());
                    format!(
                        r#"{{
  "extends": "{}",
  "compilerOptions": {{ "rootDir": "src", "outDir": "dist" }},
  "include": ["src/**/*", "**/*.ts", "**/*.tsx", "**/*.js", "**/*.jsx"]
}}"#,
                        extends_path
                    )
                } else {
                    r#"{
  "compilerOptions": {
    "allowJs": true,
    "jsx": "react-jsx",
    "esModuleInterop": true,
    "moduleResolution": "node"
  },
  "include": ["src/**/*", "**/*.ts", "**/*.tsx", "**/*.js", "**/*.jsx"]
}"#
                    .to_string()
                };

                let tsconfig_path = entry.join("tsconfig.json");
                match std::fs::write(&tsconfig_path, &tsconfig_content) {
                    Ok(()) => {
                        let rel = entry.strip_prefix(workspace).unwrap_or(&entry);
                        info!("Generated tsconfig.json for workspace package {:?}", rel.display());
                        generated_count += 1;
                    }
                    Err(e) => {
                        warn!("Failed to write tsconfig.json in {:?}: {}", entry, e);
                    }
                }
            }
        }

        if generated_count > 0 {
            info!(
                "Generated tsconfig.json for {} workspace package(s) missing it",
                generated_count
            );
        }
    }

    /// Remove version-gating fields from the workspace copy's package.json.
    /// Projects pin PM versions via "packageManager" (Corepack) and "engines" (pnpm/yarn/npm).
    /// scip-typescript shells out to PMs internally and these constraints cause hard failures
    /// when the bundled PM version doesn't match. We strip them since we only need
    /// node_modules for resolution, not a production build.
    fn sanitize_package_json(&self, workspace: &Path) {
        let pkg_path = workspace.join("package.json");
        let contents = match std::fs::read_to_string(&pkg_path) {
            Ok(c) => c,
            Err(_) => return,
        };
        let mut pkg: serde_json::Value = match serde_json::from_str(&contents) {
            Ok(v) => v,
            Err(_) => return,
        };

        let obj = match pkg.as_object_mut() {
            Some(o) => o,
            None => return,
        };

        let mut changed = false;

        // Strip packageManager (e.g. "yarn@4.6.0") — prevents Corepack/PM version rejection
        if obj.remove("packageManager").is_some() {
            info!("Stripped packageManager field from workspace package.json");
            changed = true;
        }

        // Strip PM version constraints from engines (e.g. "pnpm": "^9.12.2")
        // Keep "node" constraint — we actually respect that via ensure_node_version().
        if let Some(engines) = obj.get_mut("engines").and_then(|v| v.as_object_mut()) {
            for key in &["pnpm", "yarn", "npm"] {
                if engines.remove(*key).is_some() {
                    info!("Stripped engines.{} constraint from workspace package.json", key);
                    changed = true;
                }
            }
        }

        if changed {
            if let Ok(updated) = serde_json::to_string_pretty(&pkg) {
                let _ = std::fs::write(&pkg_path, updated);
            }
        }
    }

    /// Delete lockfiles whose format is incompatible with our bundled PM versions.
    /// Lockfile formats change across major PM versions (Yarn 1 vs Berry, pnpm v6 vs v9).
    /// Without a lockfile the PM resolves from the registry — slower but always works.
    fn sanitize_lockfiles(&self, workspace: &Path) {
        // Yarn Berry (v2+) lockfile starts with "__metadata:" — Yarn 1 can't parse it.
        let yarn_lock = workspace.join("yarn.lock");
        if yarn_lock.exists() {
            if let Ok(contents) = std::fs::read_to_string(&yarn_lock) {
                // Yarn 1 lockfile starts with "# THIS IS AN AUTOGENERATED FILE"
                // Yarn Berry lockfile starts with "__metadata:" (YAML format)
                let is_berry = contents.trim_start().starts_with("__metadata:");
                if is_berry {
                    info!("Detected Yarn Berry lockfile format — removing for Yarn 1 compatibility");
                    let _ = std::fs::remove_file(&yarn_lock);
                }
            }
        }

        // pnpm lockfile v6+ uses a different format than older versions.
        // Check if our bundled pnpm can handle it; if not, remove.
        let pnpm_lock = workspace.join("pnpm-lock.yaml");
        if pnpm_lock.exists() {
            if let Ok(contents) = std::fs::read_to_string(&pnpm_lock) {
                // Extract lockfileVersion — versions >= 9.0 may be incompatible with older pnpm
                if let Some(version_line) = contents.lines().find(|l| l.starts_with("lockfileVersion:")) {
                    let version_str = version_line.trim_start_matches("lockfileVersion:").trim().trim_matches('\'').trim_matches('"');
                    if let Ok(version) = version_str.parse::<f64>() {
                        if version >= 9.0 {
                            info!("Detected pnpm lockfile v{} — removing for bundled pnpm compatibility", version);
                            let _ = std::fs::remove_file(&pnpm_lock);
                        }
                    }
                }
            }
        }
    }

    /// For pnpm monorepos, synthesize a "workspaces" field in package.json from
    /// pnpm-workspace.yaml. This lets us always use --yarn-workspaces (Yarn 1) for
    /// workspace discovery, avoiding scip-typescript's internal `pnpm ls -r` call
    /// which fails on PM version mismatches.
    fn ensure_pnpm_workspaces(&self, workspace: &Path) {
        if self.has_workspaces_field(workspace) {
            return; // Already has workspaces — nothing to do
        }

        let pnpm_ws_path = workspace.join("pnpm-workspace.yaml");
        if !pnpm_ws_path.exists() {
            return;
        }

        let contents = match std::fs::read_to_string(&pnpm_ws_path) {
            Ok(c) => c,
            Err(_) => return,
        };

        // Parse pnpm-workspace.yaml — format is: packages:\n  - 'glob'\n  - 'glob'
        let mut globs: Vec<String> = Vec::new();
        let mut in_packages = false;
        for line in contents.lines() {
            let trimmed = line.trim();
            if trimmed == "packages:" {
                in_packages = true;
                continue;
            }
            if in_packages {
                if trimmed.starts_with('-') {
                    let glob = trimmed.trim_start_matches('-').trim()
                        .trim_matches('\'').trim_matches('"');
                    if !glob.is_empty() {
                        globs.push(glob.to_string());
                    }
                } else if !trimmed.is_empty() {
                    break; // New top-level key
                }
            }
        }

        if globs.is_empty() {
            return;
        }

        info!(
            "Synthesizing workspaces field from pnpm-workspace.yaml ({} globs)",
            globs.len()
        );

        let pkg_path = workspace.join("package.json");
        let pkg_contents = match std::fs::read_to_string(&pkg_path) {
            Ok(c) => c,
            Err(_) => return,
        };
        let mut pkg: serde_json::Value = match serde_json::from_str(&pkg_contents) {
            Ok(v) => v,
            Err(_) => return,
        };

        let workspace_values: Vec<serde_json::Value> = globs
            .iter()
            .map(|g| serde_json::Value::String(g.clone()))
            .collect();
        pkg["workspaces"] = serde_json::Value::Array(workspace_values);

        if let Ok(updated) = serde_json::to_string_pretty(&pkg) {
            if let Err(e) = std::fs::write(&pkg_path, updated) {
                warn!("Failed to write workspaces field to package.json: {}", e);
            }
        }
    }

    /// For NX monorepos that don't have a "workspaces" field in package.json,
    /// synthesize one by scanning for project.json files. This lets yarn/pnpm link
    /// workspace packages and lets scip-typescript discover them.
    fn ensure_nx_workspaces(&self, workspace: &Path) {
        let pkg_path = workspace.join("package.json");
        let pkg_contents = match std::fs::read_to_string(&pkg_path) {
            Ok(c) => c,
            Err(_) => return,
        };
        let mut pkg: serde_json::Value = match serde_json::from_str(&pkg_contents) {
            Ok(v) => v,
            Err(_) => return,
        };

        // Already has workspaces — nothing to do
        if pkg.get("workspaces").is_some() {
            return;
        }

        if !workspace.join("nx.json").exists() {
            return;
        }

        // Scan for project.json files to discover NX projects
        let mut workspace_dirs: Vec<String> = Vec::new();
        for entry in walkdir::WalkDir::new(workspace)
            .max_depth(4)
            .into_iter()
            .filter_map(|e| e.ok())
        {
            if entry.file_name() == "project.json" && entry.depth() > 0 {
                if let Some(parent) = entry.path().parent() {
                    if let Ok(rel) = parent.strip_prefix(workspace) {
                        let rel_str = rel.to_string_lossy().to_string();
                        if !rel_str.is_empty() && !rel_str.contains("node_modules") {
                            workspace_dirs.push(rel_str);
                        }
                    }
                }
            }
        }

        if workspace_dirs.is_empty() {
            return;
        }

        info!(
            "Synthesizing workspaces field from {} NX project.json files",
            workspace_dirs.len()
        );

        let workspace_values: Vec<serde_json::Value> = workspace_dirs
            .iter()
            .map(|d| serde_json::Value::String(d.clone()))
            .collect();
        pkg["workspaces"] = serde_json::Value::Array(workspace_values);

        match serde_json::to_string_pretty(&pkg) {
            Ok(updated) => {
                if let Err(e) = std::fs::write(&pkg_path, updated) {
                    warn!("Failed to write updated package.json with workspaces: {}", e);
                }
            }
            Err(e) => warn!("Failed to serialize package.json: {}", e),
        }
    }

    /// Generate minimal package.json for NX workspace dirs missing one.
    /// Yarn needs package.json to see workspace members; NX apps often only have project.json.
    fn ensure_nx_package_jsons(&self, workspace: &Path) {
        if !workspace.join("nx.json").exists() {
            return;
        }

        let pkg_path = workspace.join("package.json");
        let pkg_contents = match std::fs::read_to_string(&pkg_path) {
            Ok(c) => c,
            Err(_) => return,
        };
        let pkg: serde_json::Value = match serde_json::from_str(&pkg_contents) {
            Ok(v) => v,
            Err(_) => return,
        };

        let workspace_globs: Vec<String> = match pkg.get("workspaces") {
            Some(serde_json::Value::Array(arr)) => {
                arr.iter().filter_map(|v| v.as_str().map(String::from)).collect()
            }
            _ => return,
        };

        if workspace_globs.is_empty() {
            return;
        }

        let mut generated_count = 0u32;

        for pattern in &workspace_globs {
            let full_pattern = format!("{}/{}", workspace.display(), pattern);
            let matches = match glob::glob(&full_pattern) {
                Ok(paths) => paths,
                Err(e) => {
                    warn!("Invalid workspace glob pattern {:?}: {}", pattern, e);
                    continue;
                }
            };

            for entry in matches.flatten() {
                if !entry.is_dir() {
                    continue;
                }
                if !entry.join("project.json").exists() {
                    continue;
                }
                if entry.join("package.json").exists() {
                    continue;
                }
                if !has_ts_js_files(&entry) {
                    continue;
                }

                let rel = entry
                    .strip_prefix(workspace)
                    .unwrap_or(&entry)
                    .to_string_lossy()
                    .replace('/', "-")
                    .replace('\\', "-");

                let pkg_json = format!(
                    r#"{{"name": "{}", "version": "0.0.0", "private": true}}"#,
                    rel
                );

                match std::fs::write(entry.join("package.json"), &pkg_json) {
                    Ok(_) => {
                        info!("Generated package.json for NX project {:?}", rel);
                        generated_count += 1;
                    }
                    Err(e) => {
                        warn!("Failed to write package.json for {:?}: {}", rel, e);
                    }
                }
            }
        }

        if generated_count > 0 {
            info!(
                "Generated {} package.json files for NX workspace dirs",
                generated_count
            );
        }
    }

    /// Add uncovered tsconfig.json directories to the workspace configuration.
    /// Also generates package.json for newly-added dirs so yarn can discover them.
    fn ensure_all_tsconfig_dirs_in_workspace(&self, workspace: &Path) {
        let pkg_path = workspace.join("package.json");
        let pkg_contents = match std::fs::read_to_string(&pkg_path) {
            Ok(c) => c,
            Err(_) => return,
        };
        let mut pkg: serde_json::Value = match serde_json::from_str(&pkg_contents) {
            Ok(v) => v,
            Err(_) => return,
        };

        let current_globs: Vec<String> = match pkg.get("workspaces") {
            Some(serde_json::Value::Array(arr)) => {
                arr.iter().filter_map(|v| v.as_str().map(String::from)).collect()
            }
            Some(serde_json::Value::Object(obj)) => {
                obj.get("packages")
                    .and_then(|v| v.as_array())
                    .map(|arr| arr.iter().filter_map(|v| v.as_str().map(String::from)).collect())
                    .unwrap_or_default()
            }
            _ => return,
        };

        let mut covered_dirs: std::collections::HashSet<PathBuf> = std::collections::HashSet::new();
        for pattern in &current_globs {
            let full_pattern = format!("{}/{}", workspace.display(), pattern);
            if let Ok(matches) = glob::glob(&full_pattern) {
                for entry in matches.flatten() {
                    if entry.is_dir() {
                        covered_dirs.insert(entry);
                    }
                }
            }
        }

        let mut new_dirs: Vec<String> = Vec::new();
        for entry in walkdir::WalkDir::new(workspace)
            .max_depth(4)
            .into_iter()
            .filter_entry(|e| {
                let name = e.file_name().to_string_lossy();
                name != "node_modules" && name != ".git" && name != "dist" && name != ".next"
            })
            .filter_map(|e| e.ok())
        {
            if entry.file_name() != "tsconfig.json" || entry.depth() == 0 {
                continue;
            }
            let parent = match entry.path().parent() {
                Some(p) => p.to_path_buf(),
                None => continue,
            };

            if covered_dirs.contains(&parent) {
                continue;
            }

            if !has_ts_js_files(&parent) {
                continue;
            }

            if let Ok(rel) = parent.strip_prefix(workspace) {
                let rel_str = rel.to_string_lossy().to_string();
                if !rel_str.is_empty() && !rel_str.contains("node_modules") {
                    new_dirs.push(rel_str);
                }
            }
        }

        if new_dirs.is_empty() {
            return;
        }

        info!(
            "Adding {} uncovered tsconfig.json directories to workspaces: {:?}",
            new_dirs.len(), new_dirs
        );

        match pkg.get_mut("workspaces") {
            Some(serde_json::Value::Array(arr)) => {
                for d in &new_dirs {
                    arr.push(serde_json::Value::String(d.clone()));
                }
            }
            Some(serde_json::Value::Object(obj)) => {
                if let Some(packages) = obj.get_mut("packages").and_then(|v| v.as_array_mut()) {
                    for d in &new_dirs {
                        packages.push(serde_json::Value::String(d.clone()));
                    }
                }
            }
            _ => return,
        }

        if let Ok(updated) = serde_json::to_string_pretty(&pkg) {
            if let Err(e) = std::fs::write(&pkg_path, updated) {
                warn!("Failed to write updated package.json with new workspace dirs: {}", e);
                return;
            }
        }

        let mut gen_count = 0u32;
        for rel_dir in &new_dirs {
            let abs_dir = workspace.join(rel_dir);
            if abs_dir.join("package.json").exists() {
                continue;
            }
            let pkg_name = rel_dir.replace('/', "-").replace('\\', "-");
            let pkg_json = format!(
                r#"{{"name": "{}", "version": "0.0.0", "private": true}}"#,
                pkg_name
            );
            match std::fs::write(abs_dir.join("package.json"), &pkg_json) {
                Ok(_) => gen_count += 1,
                Err(e) => warn!("Failed to write package.json for {:?}: {}", rel_dir, e),
            }
        }
        if gen_count > 0 {
            info!("Generated {} package.json files for new workspace dirs", gen_count);
        }
    }

    /// Run scip-java with automatic build tool disambiguation.
    /// scip-java errors when both Maven and Gradle are detected; we pick one.
    fn run_java_indexer(&self, output: &Path) -> Result<()> {
        let output_str = output.to_str().unwrap();

        // Detect available build tools
        let has_maven = self.codebase_path.join("pom.xml").exists()
            || self.codebase_path.join("mvnw").exists();
        let has_gradle = self.codebase_path.join("build.gradle").exists()
            || self.codebase_path.join("build.gradle.kts").exists()
            || self.codebase_path.join("gradlew").exists();
        let has_sbt = self.codebase_path.join("build.sbt").exists();

        // If multiple build tools detected, disambiguate with --build-tool flag.
        // Priority: wrapper scripts > config files (wrapper = project's chosen tool)
        let build_tool_count = [has_maven, has_gradle, has_sbt].iter().filter(|&&b| b).count();
        if build_tool_count > 1 {
            let tool = if self.codebase_path.join("mvnw").exists() {
                "maven"
            } else if self.codebase_path.join("gradlew").exists() {
                "gradle"
            } else if has_sbt {
                "sbt"
            } else if has_maven {
                "maven"
            } else {
                "gradle"
            };
            info!("Multiple JVM build tools detected, using --build-tool={}", tool);
            let build_flag = format!("--build-tool={}", tool);
            return self.run_simple_indexer(
                "scip-java",
                &["index", "--output", output_str, &build_flag],
            );
        }

        self.run_simple_indexer("scip-java", &["index", "--output", output_str])
    }

    /// Try running scip-typescript from a specific directory.
    /// Attempts bundled binary → npx → direct command, in order.
    fn try_typescript_from_dir(&self, args: &[&str], dir: &Path) -> Result<()> {
        // Try bundled first
        if let Some(bundled) = self.get_bundled_path("scip-typescript") {
            return self.execute_indexer_in(bundled.to_str().unwrap(), args, dir);
        }

        // Try npx
        if which::which("npx").is_ok() {
            debug!("Using npx to run scip-typescript from {:?}", dir);
            let mut npx_args = vec!["@sourcegraph/scip-typescript"];
            npx_args.extend(args.iter());

            let status = Command::new("npx")
                .current_dir(dir)
                .args(&npx_args)
                .status()
                .context("Failed to run npx scip-typescript")?;

            if status.success() {
                return Ok(());
            }
        }

        // Try direct command
        self.execute_indexer_in("scip-typescript", args, dir)
    }

    /// Run scip-python with auto-detected source roots and best-effort pip install.
    /// Sourcegraph pattern: pip install . || true + PYTHONPATH for source root resolution.
    fn run_python_indexer(&self, output: &Path) -> Result<()> {
        let output_str = output.to_str().unwrap();

        // Detect source roots for PYTHONPATH
        let source_roots = self.detect_python_source_roots();

        // Best-effort pip install for third-party import resolution
        let python_libs = self.output_dir.join("python-libs");
        std::fs::create_dir_all(&python_libs).ok();
        self.try_install_python_deps(&python_libs);

        // Build PYTHONPATH from installed libs + source roots
        let mut pythonpath_parts: Vec<String> = Vec::new();
        if python_libs.exists() {
            pythonpath_parts.push(python_libs.to_str().unwrap().to_string());
        }
        for root in &source_roots {
            pythonpath_parts.push(root.to_str().unwrap().to_string());
        }

        // Run scip-python
        let binary = self.get_bundled_path("scip-python")
            .unwrap_or_else(|| PathBuf::from("scip-python"));
        let mut cmd = Command::new(&binary);
        cmd.current_dir(&self.codebase_path)
            .env("PYTHONDONTWRITEBYTECODE", "1")
            .args(["index", ".", "--output", output_str]);

        if !pythonpath_parts.is_empty() {
            let pythonpath = pythonpath_parts.join(":");
            info!("Setting PYTHONPATH for scip-python: {}", pythonpath);
            cmd.env("PYTHONPATH", &pythonpath);
        }

        let status = cmd.status().context("Failed to run scip-python")?;
        if status.success() {
            Ok(())
        } else {
            Err(anyhow!("scip-python failed with exit code {:?}", status.code()))
        }
    }

    /// Auto-detect Python source roots (directories that should be on PYTHONPATH).
    /// Many projects have source dirs like backend/src/, app/, lib/ where imports
    /// like `from models import ...` only resolve if Pyright knows the source root.
    fn detect_python_source_roots(&self) -> Vec<PathBuf> {
        let mut roots = Vec::new();
        let common_dirs = ["src", "lib", "app", "backend", "backend/src",
                           "server", "api", "core"];
        for dir in &common_dirs {
            let candidate = self.codebase_path.join(dir);
            if candidate.is_dir() {
                let has_py = candidate.join("__init__.py").exists()
                    || std::fs::read_dir(&candidate).ok()
                        .map(|entries| entries.flatten().any(|e|
                            e.path().extension().is_some_and(|ext| ext == "py")))
                        .unwrap_or(false);
                if has_py {
                    roots.push(candidate);
                }
            }
        }
        // Also detect top-level packages (dirs with __init__.py at depth 1)
        // and add their parent (the codebase root) as a source root
        if let Ok(entries) = std::fs::read_dir(&self.codebase_path) {
            for entry in entries.flatten() {
                let path = entry.path();
                if path.is_dir() && path.join("__init__.py").exists() {
                    let parent = path.parent().unwrap_or(&self.codebase_path).to_path_buf();
                    if !roots.contains(&parent) {
                        roots.push(parent);
                    }
                }
            }
        }
        if !roots.is_empty() {
            info!("Detected Python source roots: {:?}", roots);
        }
        roots
    }

    /// Best-effort pip install for third-party import resolution.
    /// Sourcegraph pattern: pip install . || true (lenient — proceed even if install fails).
    fn try_install_python_deps(&self, target: &Path) {
        let req_txt = self.codebase_path.join("requirements.txt");
        let pyproject = self.codebase_path.join("pyproject.toml");
        if !req_txt.exists() && !pyproject.exists() {
            return;
        }
        info!("Installing Python dependencies for import resolution...");
        let status = if req_txt.exists() {
            Command::new("pip3")
                .args(["install", "--target", target.to_str().unwrap(),
                       "--no-deps", "--quiet", "--disable-pip-version-check",
                       "-r", req_txt.to_str().unwrap()])
                .current_dir(&self.codebase_path)
                .status()
        } else {
            Command::new("pip3")
                .args(["install", "--target", target.to_str().unwrap(),
                       "--no-deps", "--quiet", "--disable-pip-version-check", "."])
                .current_dir(&self.codebase_path)
                .status()
        };
        match status {
            Ok(s) if s.success() => info!("Python dependencies installed"),
            _ => warn!("pip install failed, continuing without third-party resolution"),
        }
    }

    /// Pre-download Go module dependencies with optional GOWORK environment.
    /// Non-fatal on failure — graceful degradation.
    fn download_go_deps_with_env(&self, module_dir: &Path, gowork_env: &Option<String>) {
        info!("Downloading Go module dependencies for {:?}...", module_dir);
        let mut cmd = Command::new("go");
        cmd.current_dir(module_dir)
            .args(["mod", "download"]);
        if let Some(ref gowork_path) = gowork_env {
            cmd.env("GOWORK", gowork_path);
        }
        let status = cmd.status();
        match status {
            Ok(s) if s.success() => info!("Go modules downloaded for {:?}", module_dir),
            _ => warn!("go mod download failed for {:?}, continuing anyway", module_dir),
        }
    }

    /// Run scip-go indexer with subdirectory module support.
    /// If root has go.mod, runs normally. Otherwise searches for go.mod files
    /// in subdirectories (max depth 3) and runs scip-go from each.
    /// For go.work monorepos: copies go.work to a writable temp dir with absolute
    /// paths so cross-module imports resolve and go.work.sum can be written.
    /// Falls back to GOWORK=off if workspace mode fails all modules.
    fn run_go_indexer(&self, output: &Path) -> Result<()> {
        let output_str = output.to_str().unwrap();

        // If root has go.mod, download deps and run normally
        if self.codebase_path.join("go.mod").exists() {
            self.download_go_deps_with_env(&self.codebase_path, &None);
            return self.run_simple_indexer("scip-go", &["--output", output_str]);
        }

        // Search for go.mod in subdirectories
        let go_mod_dirs = self.find_go_modules();
        if go_mod_dirs.is_empty() {
            return Err(anyhow!("No go.mod found in codebase"));
        }

        // If there's a go.work file, create a writable copy with absolute paths.
        // This lets cross-module imports resolve while go.work.sum gets written
        // to writable temp space instead of the read-only codebase mount.
        let gowork_env = self.create_writable_gowork();

        info!("Found {} Go modules in subdirectories", go_mod_dirs.len());

        // First pass: try with workspace context (GOWORK set)
        let mut any_success = false;
        if gowork_env.is_some() {
            any_success = self.run_go_modules_with_env(&go_mod_dirs, output, &gowork_env);
            if !any_success {
                warn!("All Go modules failed with workspace mode, retrying with GOWORK=off");
            }
        }

        // Second pass: if workspace mode failed (or no go.work), try GOWORK=off
        // Each module is indexed independently — less cross-module precision but
        // still captures intra-module symbols and references.
        if !any_success {
            let gowork_off = Some("off".to_string());
            any_success = self.run_go_modules_with_env(&go_mod_dirs, output, &gowork_off);
        }

        if any_success {
            Ok(())
        } else {
            Err(anyhow!("All Go module indexing failed"))
        }
    }

    /// Run scip-go on each Go module directory, rewrite paths to repo-relative,
    /// and merge all outputs. Returns true if at least one succeeded.
    fn run_go_modules_with_env(
        &self, go_mod_dirs: &[PathBuf], output: &Path, gowork_env: &Option<String>,
    ) -> bool {
        let mut successful_outputs: Vec<PathBuf> = Vec::new();

        for (i, dir) in go_mod_dirs.iter().enumerate() {
            // Writable copy needed for go.sum writes on read-only mounts
            let writable_dir = self.create_writable_go_module(dir, i);
            let work_dir = writable_dir.as_deref().unwrap_or(dir);

            self.download_go_deps_with_env(work_dir, gowork_env);

            let sub_output = self.output_dir.join(format!("go-{}.scip", i));
            let sub_output_str = sub_output.to_str().unwrap();

            let binary = self.get_bundled_path("scip-go")
                .unwrap_or_else(|| PathBuf::from("scip-go"));
            let mut cmd = Command::new(&binary);
            // Run from original dir (Go resolves modules via real paths, not symlinks)
            cmd.current_dir(dir)
                .args(["--output", sub_output_str]);
            if let Some(ref gowork_path) = gowork_env {
                cmd.env("GOWORK", gowork_path);
            }

            match cmd.output().with_context(|| format!("Failed to run scip-go in {:?}", dir)) {
                Ok(output_result) if output_result.status.success() => {
                    info!("scip-go succeeded for {:?}", dir);
                    if sub_output.exists() {
                        if let Ok(rel) = dir.strip_prefix(&self.codebase_path) {
                            let rel_str = rel.to_string_lossy();
                            if !rel_str.is_empty() {
                                let prefix = format!("{}/", rel_str.trim_end_matches('/'));
                                info!("Rewriting SCIP paths with prefix {:?}", prefix);
                                if let Err(e) = Self::rewrite_scip_paths(&sub_output, &prefix) {
                                    warn!("Failed to rewrite SCIP paths for {:?}: {}", dir, e);
                                }
                            }
                        }
                        successful_outputs.push(sub_output);
                    }
                }
                Ok(output_result) => {
                    let stderr = String::from_utf8_lossy(&output_result.stderr);
                    warn!("scip-go failed for {:?}: exit status {:?}\nstderr: {}", dir, output_result.status.code(), stderr);
                }
                Err(e) => warn!("scip-go failed for {:?}: {}", dir, e),
            }
        }

        if successful_outputs.is_empty() {
            return false;
        }

        if successful_outputs.len() == 1 {
            if let Err(e) = std::fs::rename(&successful_outputs[0], output) {
                warn!("Failed to rename Go sub-output: {}", e);
                return false;
            }
        } else {
            info!("Merging {} Go SCIP outputs into {:?}", successful_outputs.len(), output);
            match Self::merge_scip_outputs(&successful_outputs, output) {
                Ok(()) => info!("Successfully merged {} Go SCIP outputs", successful_outputs.len()),
                Err(e) => {
                    warn!("Failed to merge Go SCIP outputs: {}, using first output only", e);
                    if let Err(e2) = std::fs::rename(&successful_outputs[0], output) {
                        warn!("Failed to rename first Go sub-output: {}", e2);
                        return false;
                    }
                }
            }
        }

        for path in &successful_outputs {
            if path.exists() {
                let _ = std::fs::remove_file(path);
            }
        }

        true
    }

    /// Merge multiple SCIP protobuf files by concatenating raw bytes.
    /// Valid because protobuf repeated fields append on concat.
    fn merge_scip_outputs(inputs: &[PathBuf], output: &Path) -> Result<()> {
        use std::io::Write;
        let mut out_file = std::fs::File::create(output)
            .context("Failed to create merged SCIP output")?;
        for input in inputs {
            let data = std::fs::read(input)
                .with_context(|| format!("Failed to read SCIP output {:?}", input))?;
            out_file.write_all(&data)
                .with_context(|| format!("Failed to write SCIP data from {:?}", input))?;
        }
        Ok(())
    }

    /// Rewrite SCIP document paths by prepending a prefix (module-relative → repo-relative).
    /// Operates at protobuf wire level: Index.documents (field 2), Document.relative_path (field 1).
    fn rewrite_scip_paths(scip_path: &Path, prefix: &str) -> Result<()> {
        let data = std::fs::read(scip_path)
            .with_context(|| format!("Failed to read SCIP file {:?}", scip_path))?;

        let rewritten = Self::rewrite_index_document_paths(&data, prefix)?;

        std::fs::write(scip_path, &rewritten)
            .with_context(|| format!("Failed to write rewritten SCIP file {:?}", scip_path))?;

        Ok(())
    }

    /// Walk Index wire format, rewrite Document relative_path fields.
    fn rewrite_index_document_paths(data: &[u8], prefix: &str) -> Result<Vec<u8>> {
        let mut output = Vec::with_capacity(data.len() + data.len() / 10);
        let mut pos = 0;

        while pos < data.len() {
            let (tag, tag_end) = Self::pb_read_varint(data, pos)
                .ok_or_else(|| anyhow!("Truncated varint at position {}", pos))?;
            let field_number = tag >> 3;
            let wire_type = tag & 0x07;

            match wire_type {
                0 => { // varint
                    let (_, val_end) = Self::pb_read_varint(data, tag_end)
                        .ok_or_else(|| anyhow!("Truncated varint value"))?;
                    output.extend_from_slice(&data[pos..val_end]);
                    pos = val_end;
                }
                1 => { // fixed64
                    let end = tag_end + 8;
                    if end > data.len() { break; }
                    output.extend_from_slice(&data[pos..end]);
                    pos = end;
                }
                2 => { // length-delimited
                    let (field_len, data_start) = Self::pb_read_varint(data, tag_end)
                        .ok_or_else(|| anyhow!("Truncated length prefix"))?;
                    let field_end = data_start + field_len as usize;
                    if field_end > data.len() { break; }

                    if field_number == 2 {
                        // Document submessage — rewrite its relative_path field
                        let doc_bytes = &data[data_start..field_end];
                        let new_doc = Self::rewrite_document_path(doc_bytes, prefix);
                        Self::pb_write_varint(&mut output, tag);
                        Self::pb_write_varint(&mut output, new_doc.len() as u64);
                        output.extend_from_slice(&new_doc);
                    } else {
                        // Non-document field (metadata, external_symbols) — copy as-is
                        output.extend_from_slice(&data[pos..field_end]);
                    }
                    pos = field_end;
                }
                5 => { // fixed32
                    let end = tag_end + 4;
                    if end > data.len() { break; }
                    output.extend_from_slice(&data[pos..end]);
                    pos = end;
                }
                _ => break,
            }
        }

        Ok(output)
    }

    /// Rewrite a single Document's relative_path (field 1) by prepending a prefix.
    fn rewrite_document_path(doc_bytes: &[u8], prefix: &str) -> Vec<u8> {
        let mut output = Vec::with_capacity(doc_bytes.len() + prefix.len());
        let mut pos = 0;

        while pos < doc_bytes.len() {
            let (tag, tag_end) = match Self::pb_read_varint(doc_bytes, pos) {
                Some(v) => v,
                None => break,
            };
            let field_number = tag >> 3;
            let wire_type = tag & 0x07;

            match wire_type {
                0 => {
                    match Self::pb_read_varint(doc_bytes, tag_end) {
                        Some((_, val_end)) => {
                            output.extend_from_slice(&doc_bytes[pos..val_end]);
                            pos = val_end;
                        }
                        None => break,
                    }
                }
                1 => {
                    let end = tag_end + 8;
                    if end > doc_bytes.len() { break; }
                    output.extend_from_slice(&doc_bytes[pos..end]);
                    pos = end;
                }
                2 => {
                    let (field_len, data_start) = match Self::pb_read_varint(doc_bytes, tag_end) {
                        Some(v) => v,
                        None => break,
                    };
                    let field_end = data_start + field_len as usize;
                    if field_end > doc_bytes.len() { break; }

                    if field_number == 1 {
                        // relative_path (string, field 1) — prepend prefix
                        let old_path = &doc_bytes[data_start..field_end];
                        // Skip paths starting with ../ (build cache artifacts)
                        if old_path.starts_with(b"../") {
                            output.extend_from_slice(&doc_bytes[pos..field_end]);
                        } else {
                            let new_len = prefix.len() + old_path.len();
                            Self::pb_write_varint(&mut output, tag);
                            Self::pb_write_varint(&mut output, new_len as u64);
                            output.extend_from_slice(prefix.as_bytes());
                            output.extend_from_slice(old_path);
                        }
                    } else {
                        output.extend_from_slice(&doc_bytes[pos..field_end]);
                    }
                    pos = field_end;
                }
                5 => {
                    let end = tag_end + 4;
                    if end > doc_bytes.len() { break; }
                    output.extend_from_slice(&doc_bytes[pos..end]);
                    pos = end;
                }
                _ => break,
            }
        }

        output
    }

    /// Read a protobuf varint from data at the given position.
    /// Returns (value, position_after_varint) or None if truncated.
    fn pb_read_varint(data: &[u8], mut pos: usize) -> Option<(u64, usize)> {
        let mut result: u64 = 0;
        let mut shift = 0;
        while pos < data.len() {
            let b = data[pos] as u64;
            result |= (b & 0x7F) << shift;
            pos += 1;
            if (b & 0x80) == 0 {
                return Some((result, pos));
            }
            shift += 7;
            if shift >= 64 { return None; }
        }
        None
    }

    /// Write a protobuf varint to a byte buffer.
    fn pb_write_varint(buf: &mut Vec<u8>, mut value: u64) {
        loop {
            let mut byte = (value & 0x7F) as u8;
            value >>= 7;
            if value != 0 {
                byte |= 0x80;
            }
            buf.push(byte);
            if value == 0 {
                break;
            }
        }
    }

    /// Create a writable copy of a Go module directory so go.sum can be written.
    /// Uses symlinks for source files to minimize disk usage and I/O.
    /// Returns the writable directory path, or None if creation fails.
    fn create_writable_go_module(&self, module_dir: &Path, index: usize) -> Option<PathBuf> {
        let writable = self.output_dir.join(format!("go-mod-{}", index));
        if let Err(e) = std::fs::create_dir_all(&writable) {
            warn!("Failed to create writable Go module dir: {}", e);
            return None;
        }

        // Copy go.mod and go.sum (these need to be writable)
        for name in &["go.mod", "go.sum"] {
            let src = module_dir.join(name);
            if src.exists() {
                let _ = std::fs::copy(&src, writable.join(name));
            }
        }

        // Symlink all other directories and files from the original module
        match std::fs::read_dir(module_dir) {
            Ok(entries) => {
                for entry in entries.flatten() {
                    let name = entry.file_name();
                    let name_str = name.to_string_lossy();
                    if name_str == "go.mod" || name_str == "go.sum" {
                        continue; // Already copied above
                    }
                    let target = writable.join(&name);
                    if !target.exists() {
                        let _ = std::os::unix::fs::symlink(entry.path(), &target);
                    }
                }
            }
            Err(e) => {
                warn!("Failed to read Go module dir {:?}: {}", module_dir, e);
                return None;
            }
        }

        Some(writable)
    }

    /// Copy go.work to a writable temp dir, converting relative paths to absolute.
    /// Returns the path to the writable go.work, or None if no go.work exists.
    fn create_writable_gowork(&self) -> Option<String> {
        let go_work_path = self.codebase_path.join("go.work");
        if !go_work_path.exists() {
            return None;
        }

        let content = std::fs::read_to_string(&go_work_path).ok()?;

        // Convert relative paths like ./apps/daemon or ../shared to absolute paths
        let mut rewritten = String::new();
        for line in content.lines() {
            let trimmed = line.trim();
            if trimmed.starts_with("./") || trimmed.starts_with("../") {
                // Use Path::join to correctly resolve both ./ and ../ relative paths
                let abs = self.codebase_path.join(trimmed);
                let abs_str = abs.to_str().unwrap_or(trimmed);
                rewritten.push_str(&format!("\t{}\n", abs_str));
            } else {
                rewritten.push_str(line);
                rewritten.push('\n');
            }
        }

        let writable_gowork = self.output_dir.join("go.work");
        if std::fs::write(&writable_gowork, &rewritten).is_err() {
            warn!("Failed to write writable go.work, falling back to GOWORK=off");
            return None;
        }

        // Also copy go.work.sum if it exists (so Go doesn't re-download everything)
        let go_work_sum = self.codebase_path.join("go.work.sum");
        if go_work_sum.exists() {
            let _ = std::fs::copy(&go_work_sum, self.output_dir.join("go.work.sum"));
        }

        let result = writable_gowork.to_str()?.to_string();
        info!("Created writable go.work at {:?}", result);
        Some(result)
    }

    /// Find directories containing go.mod files (up to 5 levels deep).
    /// Depth 5 handles nested modules like apps/otel-collector/exporter/go.mod.
    fn find_go_modules(&self) -> Vec<PathBuf> {
        let mut results = Vec::new();
        for entry in walkdir::WalkDir::new(&self.codebase_path)
            .max_depth(5)
            .into_iter()
            .flatten()
        {
            if entry.file_name() == "go.mod" {
                if let Some(parent) = entry.path().parent() {
                    results.push(parent.to_path_buf());
                }
            }
        }
        results
    }

    /// Run scip-php indexer with writable workspace.
    /// scip-php requires the project's vendor/autoload.php which is typically gitignored.
    /// Creates a writable workspace with symlinks to codebase content, runs composer install
    /// to generate vendor/, then indexes from there.
    /// Note: scip-php does NOT support --output; it writes index.scip to cwd.
    fn run_php_indexer(&self, output: &Path) -> Result<()> {
        let has_vendor = self.codebase_path.join("vendor/autoload.php").exists();

        // Check composer.json exists (needed for workspace setup when vendor is absent)
        if !has_vendor && !self.codebase_path.join("composer.json").exists() {
            return Err(anyhow!("No composer.json found in codebase"));
        }

        // Always use a writable workspace — never run scip-php in the user's codebase
        // (scip-php writes index.scip to cwd).
        info!("Creating writable PHP workspace (zero-write guarantee)");

        let workspace = self.output_dir.join("php-workspace");
        if workspace.exists() {
            std::fs::remove_dir_all(&workspace)
                .context("Failed to clean previous php-workspace")?;
        }
        std::fs::create_dir_all(&workspace)
            .context("Failed to create php-workspace")?;

        // Symlink all top-level entries from codebase into workspace
        for entry in std::fs::read_dir(&self.codebase_path)
            .context("Failed to read codebase directory")?
            .flatten()
        {
            let name = entry.file_name();
            let name_str = name.to_string_lossy();

            // Skip composer files (copied as real files) and hidden dirs.
            // vendor/ is symlinked below only when it already exists in the codebase.
            if name_str == "vendor"
                || name_str == "composer.json"
                || name_str == "composer.lock"
                || name_str.starts_with('.')
            {
                continue;
            }

            let target = workspace.join(&name);
            std::os::unix::fs::symlink(entry.path(), &target)
                .with_context(|| format!("Failed to symlink {:?}", name))?;
        }

        if has_vendor {
            // Symlink existing vendor/ from codebase into workspace
            info!("Symlinking existing vendor/ from codebase into workspace");
            std::os::unix::fs::symlink(
                self.codebase_path.join("vendor"),
                workspace.join("vendor"),
            ).context("Failed to symlink vendor/")?;
        } else {
            // Copy only composer.json (skip composer.lock — lock files often require specific
            // PHP versions that don't match the container; fresh resolve is more reliable)
            std::fs::copy(
                self.codebase_path.join("composer.json"),
                workspace.join("composer.json"),
            ).context("Failed to copy composer.json")?;

            // Augment autoload config so scip-php discovers files outside registered PSR-4 roots
            if let Err(e) = self.augment_php_autoload(&workspace) {
                warn!("Failed to augment PHP autoload (non-fatal): {}", e);
            }

            // Run composer install to generate vendor/autoload.php
            info!("Running composer install in PHP workspace");
            let composer_status = Command::new("composer")
                .current_dir(&workspace)
                .args(["install", "--no-dev", "--no-scripts", "--no-interaction", "--ignore-platform-reqs"])
                .status()
                .context("Failed to run composer install")?;

            if !composer_status.success() {
                warn!("composer install failed (exit {:?}), trying scip-php anyway", composer_status.code());
            }
        }

        // Run scip-php from the workspace (writes index.scip to cwd, then we move it)
        self.run_indexer_and_move("scip-php", &[], &workspace, output)
    }

    /// Augment composer.json autoload so scip-php discovers PHP files outside
    /// the registered PSR-4/PSR-0 roots.  Many projects (e.g. Appwrite) only
    /// register `src/` in autoload but have controllers in `app/`, scripts in
    /// `bin/`, etc.  We add uncovered directories to `autoload.classmap`.
    ///
    /// IMPORTANT: We only use `classmap` (scanned statically for class/interface/trait
    /// declarations) — NOT `files` (which Composer `require()`s at bootstrap time).
    /// Adding procedural PHP files to `files` causes fatal crashes when those files
    /// reference runtime constants or functions not yet defined (e.g. Appwrite's
    /// `app/init/database/formats.php` uses `APP_DATABASE_ATTRIBUTE_EMAIL`).
    ///
    /// Only modifies the *workspace* copy — the real codebase is never touched.
    fn augment_php_autoload(&self, workspace: &Path) -> Result<()> {
        let composer_path = workspace.join("composer.json");
        let raw = std::fs::read_to_string(&composer_path)
            .context("Failed to read workspace composer.json")?;
        let mut root: serde_json::Value =
            serde_json::from_str(&raw).context("Failed to parse composer.json")?;

        // Collect directories already covered by existing autoload entries
        let mut covered_prefixes: Vec<String> = Vec::new();
        if let Some(autoload) = root.get("autoload") {
            for key in &["psr-4", "psr-0", "classmap"] {
                if let Some(section) = autoload.get(key) {
                    match section {
                        serde_json::Value::Object(map) => {
                            for v in map.values() {
                                match v {
                                    serde_json::Value::String(s) => {
                                        covered_prefixes.push(s.trim_end_matches('/').to_string());
                                    }
                                    serde_json::Value::Array(arr) => {
                                        for item in arr {
                                            if let Some(s) = item.as_str() {
                                                covered_prefixes.push(s.trim_end_matches('/').to_string());
                                            }
                                        }
                                    }
                                    _ => {}
                                }
                            }
                        }
                        serde_json::Value::Array(arr) => {
                            for item in arr {
                                if let Some(s) = item.as_str() {
                                    covered_prefixes.push(s.trim_end_matches('/').to_string());
                                }
                            }
                        }
                        _ => {}
                    }
                }
            }
        }

        // Walk the real codebase looking for .php files not covered by existing autoload
        let mut uncovered_dirs: Vec<String> = Vec::new();
        let mut seen_dirs = std::collections::HashSet::new();

        for entry in walkdir::WalkDir::new(&self.codebase_path)
            .max_depth(6)
            .into_iter()
            .flatten()
        {
            let path = entry.path();
            if !path.is_file() {
                continue;
            }
            if path.extension().and_then(|e| e.to_str()) != Some("php") {
                continue;
            }

            // Build relative path from codebase root
            let rel = match path.strip_prefix(&self.codebase_path) {
                Ok(r) => r.to_string_lossy().to_string(),
                Err(_) => continue,
            };

            // Skip vendor/ and tests/ directories
            if rel.starts_with("vendor/") || rel.starts_with("tests/") || rel.starts_with("test/") {
                continue;
            }

            // Check if already covered by an existing autoload prefix
            let is_covered = covered_prefixes.iter().any(|prefix| {
                rel.starts_with(prefix) || rel.starts_with(&format!("{}/", prefix))
            });
            if is_covered {
                continue;
            }

            // Add the containing directory to classmap (scanned statically, not executed)
            if let Some(parent) = path.parent() {
                if let Ok(rel_dir) = parent.strip_prefix(&self.codebase_path) {
                    let dir_str = rel_dir.to_string_lossy().to_string();
                    if !dir_str.is_empty() && seen_dirs.insert(dir_str.clone()) {
                        uncovered_dirs.push(dir_str);
                    }
                }
            }
        }

        if uncovered_dirs.is_empty() {
            info!("PHP autoload: all PHP files already covered by existing autoload entries");
            return Ok(());
        }

        info!(
            "PHP autoload augmentation: adding {} directories to classmap",
            uncovered_dirs.len()
        );

        // Ensure autoload section exists
        let autoload = root
            .as_object_mut()
            .ok_or_else(|| anyhow!("composer.json root is not an object"))?
            .entry("autoload")
            .or_insert_with(|| serde_json::json!({}));

        let autoload_obj = autoload
            .as_object_mut()
            .ok_or_else(|| anyhow!("autoload is not an object"))?;

        // Add uncovered directories to classmap only.
        // classmap is scanned statically for class/interface/trait declarations
        // without executing the PHP files, so it's safe even for files with
        // runtime dependencies.
        let classmap = autoload_obj
            .entry("classmap")
            .or_insert_with(|| serde_json::json!([]));
        if let Some(arr) = classmap.as_array_mut() {
            for dir in &uncovered_dirs {
                arr.push(serde_json::Value::String(dir.clone()));
            }
        }

        // Write modified composer.json back to workspace
        let output = serde_json::to_string_pretty(&root)
            .context("Failed to serialize modified composer.json")?;
        std::fs::write(&composer_path, output)
            .context("Failed to write augmented composer.json")?;

        Ok(())
    }

    /// Run scip-clang indexer.
    /// Requires compile_commands.json to be present. Does NOT support --output;
    /// writes index.scip to cwd.
    fn run_clang_indexer(&self, output: &Path) -> Result<()> {
        // Find compile_commands.json (required by scip-clang)
        // Always use absolute paths and run from output_dir to avoid writing to codebase.
        let compdb = self.codebase_path.join("compile_commands.json");
        if !compdb.exists() {
            // Check build/ subdirectory (CMake default)
            let build_compdb = self.codebase_path.join("build/compile_commands.json");
            if build_compdb.exists() {
                let compdb_str = build_compdb.to_string_lossy().to_string();
                let args_str = format!("--compdb-path={}", compdb_str);
                return self.run_indexer_and_move("scip-clang", &[&args_str], &self.output_dir, output);
            }
            return Err(anyhow!(
                "compile_commands.json not found. Generate it with CMake (-DCMAKE_EXPORT_COMPILE_COMMANDS=ON), \
                 Bear (bear -- make), or Bazel"
            ));
        }

        let compdb_str = compdb.to_string_lossy().to_string();
        let args_str = format!("--compdb-path={}", compdb_str);
        self.run_indexer_and_move("scip-clang", &[&args_str], &self.output_dir, output)
    }

    /// Run scip-dart indexer via dart pub global run.
    /// scip-dart does NOT support --output; writes index.scip to cwd.
    fn run_dart_indexer(&self, output: &Path) -> Result<()> {
        // scip-dart is a Dart package invoked via `dart pub global run scip_dart ./`
        // It writes index.scip to cwd, so we run it and move the file
        if which::which("dart").is_err() {
            return Err(anyhow!("Dart SDK not found. Install from https://dart.dev/get-dart"));
        }

        debug!("Running scip-dart via dart pub global run");
        let codebase_abs = self.codebase_path.to_string_lossy().to_string();
        let status = Command::new("dart")
            .current_dir(&self.output_dir)
            .args(["pub", "global", "run", "scip_dart", &codebase_abs])
            .status()
            .context("Failed to run dart pub global run scip_dart")?;

        if !status.success() {
            return Err(anyhow!("scip-dart exited with status: {:?}", status.code()));
        }

        // Move index.scip from output_dir to our output path
        let default_output = self.output_dir.join("index.scip");
        if default_output.exists() {
            std::fs::rename(&default_output, output)
                .with_context(|| format!("Failed to move index.scip to {:?}", output))?;
        } else {
            return Err(anyhow!("scip-dart did not produce index.scip"));
        }

        Ok(())
    }

    /// Run a simple indexer: try bundled path first, then fall back to PATH
    fn run_simple_indexer(&self, binary: &str, args: &[&str]) -> Result<()> {
        if let Some(bundled) = self.get_bundled_path(binary) {
            return self.execute_indexer(bundled.to_str().unwrap(), args);
        }
        self.execute_indexer(binary, args)
    }

    /// Run an indexer that doesn't support --output, then move its default
    /// index.scip to the desired output path.
    ///
    /// Safety: `working_dir` must NOT be inside the user's codebase. This is
    /// enforced with a debug assertion to catch regressions.
    fn run_indexer_and_move(&self, binary: &str, args: &[&str], working_dir: &Path, output: &Path) -> Result<()> {
        debug_assert!(
            !working_dir.starts_with(&self.codebase_path),
            "run_indexer_and_move: working_dir ({:?}) must not be inside codebase_path ({:?})",
            working_dir, self.codebase_path
        );
        self.execute_indexer_in(binary, args, working_dir)?;

        let default_output = working_dir.join("index.scip");
        if default_output.exists() {
            std::fs::rename(&default_output, output)
                .with_context(|| format!("Failed to move index.scip to {:?}", output))?;
        } else {
            return Err(anyhow!("{} did not produce index.scip in {:?}", binary, working_dir));
        }
        Ok(())
    }

    /// Run scip-dotnet indexer (special: solution file discovery + multiple fallbacks)
    fn run_dotnet_indexer(&self, output: &Path) -> Result<()> {
        let solution_file = self.find_dotnet_solution();

        let mut args = vec!["index"];
        let solution_str;
        if let Some(ref sln) = solution_file {
            solution_str = sln.to_string_lossy().to_string();
            args.push(&solution_str);
        }
        args.push("--output");
        let output_str = output.to_str().unwrap();
        args.push(output_str);

        if let Some(bundled) = self.get_bundled_path("scip-dotnet") {
            return self.execute_indexer(bundled.to_str().unwrap(), &args);
        }

        if which::which("scip-dotnet").is_ok() {
            return self.execute_indexer("scip-dotnet", &args);
        }

        // Try global dotnet tools location
        let home = std::env::var("HOME").unwrap_or_default();
        let global_tool = PathBuf::from(&home).join(".dotnet/tools/scip-dotnet");
        if global_tool.exists() {
            return self.execute_indexer(global_tool.to_str().unwrap(), &args);
        }

        // Fallback to dotnet tool run (requires local manifest)
        let mut cmd_args: Vec<&str> = vec!["tool", "run", "scip-dotnet", "--"];
        for arg in &args {
            cmd_args.push(arg);
        }

        let status = Command::new("dotnet")
            .current_dir(&self.output_dir)
            .args(&cmd_args)
            .status()
            .context("Failed to run dotnet scip-dotnet")?;

        if status.success() {
            Ok(())
        } else {
            Err(anyhow!("scip-dotnet exited with non-zero status"))
        }
    }

    /// Find a file with the given extension in a directory
    fn find_file_with_ext(&self, dir: &Path, ext: &str) -> Option<PathBuf> {
        std::fs::read_dir(dir).ok()?.flatten().find_map(|entry| {
            let path = entry.path();
            path.extension().is_some_and(|e| e == ext).then_some(path)
        })
    }

    /// Find a .sln or .csproj file in the codebase
    fn find_dotnet_solution(&self) -> Option<PathBuf> {
        // Check for .sln in root
        if let Some(sln) = self.find_file_with_ext(&self.codebase_path, "sln") {
            return Some(sln);
        }

        // Check common subdirectories for .sln
        for subdir in &["src", "source", "Source", "Src"] {
            let dir = self.codebase_path.join(subdir);
            if dir.exists() {
                if let Some(sln) = self.find_file_with_ext(&dir, "sln") {
                    return Some(sln);
                }
            }
        }

        // Check for .csproj in root
        self.find_file_with_ext(&self.codebase_path, "csproj")
    }

    /// Get path to bundled indexer if it exists
    fn get_bundled_path(&self, indexer: &str) -> Option<PathBuf> {
        self.indexers_path.as_ref().and_then(|base| {
            let path = base.join(indexer);
            path.exists().then_some(path)
        })
    }

    /// Execute an indexer binary
    fn execute_indexer(&self, binary: &str, args: &[&str]) -> Result<()> {
        self.execute_indexer_in(binary, args, &self.codebase_path)
    }

    /// Execute an indexer binary in a specific working directory
    fn execute_indexer_in(&self, binary: &str, args: &[&str], working_dir: &Path) -> Result<()> {
        debug!("Executing: {} {:?} (in {:?})", binary, args, working_dir);

        let status = Command::new(binary)
            .current_dir(working_dir)
            .args(args)
            .status()
            .with_context(|| format!("Failed to run {}", binary))?;

        if status.success() {
            Ok(())
        } else {
            Err(anyhow!("{} exited with status: {:?}", binary, status.code()))
        }
    }

    /// Get the output path for a given language's SCIP file
    pub fn scip_output_path(&self, language: Language) -> PathBuf {
        self.output_dir.join(format!("{}.scip", language.scip_output_stem()))
    }

    /// Get the output directory for SCIP files
    pub fn output_dir(&self) -> &Path {
        &self.output_dir
    }

    /// Clean up generated SCIP files
    pub fn cleanup(&self) -> Result<()> {
        if self.output_dir.exists() {
            std::fs::remove_dir_all(&self.output_dir)?;
        }
        Ok(())
    }
}

/// Check which indexers are available on the system
pub fn check_available_indexers() -> HashMap<Language, bool> {
    let temp_orchestrator = IndexerOrchestrator {
        indexers_path: None,
        codebase_path: PathBuf::from("."),
        output_dir: PathBuf::from("."),
    };

    Language::ALL
        .iter()
        .map(|&lang| (lang, temp_orchestrator.is_indexer_available(lang)))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_language_indexer_metadata() {
        assert_eq!(Language::TypeScript.scip_indexer(), "scip-typescript");
        assert!(Language::TypeScript.is_bundled());
        assert!(!Language::Ruby.is_bundled());
    }

    fn create_file(path: &Path, content: &str) {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).unwrap();
        }
        std::fs::write(path, content).unwrap();
    }

    fn make_orchestrator(workspace: &Path) -> IndexerOrchestrator {
        IndexerOrchestrator {
            indexers_path: None,
            codebase_path: workspace.to_path_buf(),
            output_dir: workspace.to_path_buf(),
        }
    }

    #[test]
    fn test_has_ts_js_files_positive() {
        let tmp = tempfile::TempDir::new().unwrap();
        let dir = tmp.path().join("myapp");
        create_file(&dir.join("src/index.ts"), "export const x = 1;");
        assert!(has_ts_js_files(&dir));
    }

    #[test]
    fn test_has_ts_js_files_negative() {
        let tmp = tempfile::TempDir::new().unwrap();
        let dir = tmp.path().join("myapp");
        create_file(&dir.join("main.go"), "package main");
        create_file(&dir.join("lib.rs"), "fn main() {}");
        assert!(!has_ts_js_files(&dir));
    }

    #[test]
    fn test_ensure_nx_package_jsons_generates_for_ts_dirs() {
        let tmp = tempfile::TempDir::new().unwrap();
        let ws = tmp.path();

        create_file(&ws.join("nx.json"), "{}");
        create_file(
            &ws.join("package.json"),
            r#"{"name": "root", "workspaces": ["apps/*", "libs/*"]}"#,
        );
        create_file(&ws.join("apps/dashboard/project.json"), "{}");
        create_file(&ws.join("apps/dashboard/src/App.tsx"), "export default () => <div/>;");

        let orch = make_orchestrator(ws);
        orch.ensure_nx_package_jsons(ws);

        let generated = ws.join("apps/dashboard/package.json");
        assert!(generated.exists(), "package.json should be generated");

        let content: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(&generated).unwrap()).unwrap();
        assert_eq!(content["name"], "apps-dashboard");
        assert_eq!(content["private"], true);
    }

    #[test]
    fn test_ensure_nx_package_jsons_skips_existing() {
        let tmp = tempfile::TempDir::new().unwrap();
        let ws = tmp.path();

        create_file(&ws.join("nx.json"), "{}");
        create_file(
            &ws.join("package.json"),
            r#"{"name": "root", "workspaces": ["libs/*"]}"#,
        );

        let existing_pkg = r#"{"name": "@myorg/ui", "version": "1.0.0"}"#;
        create_file(&ws.join("libs/ui/project.json"), "{}");
        create_file(&ws.join("libs/ui/package.json"), existing_pkg);
        create_file(&ws.join("libs/ui/src/index.ts"), "export const x = 1;");

        let orch = make_orchestrator(ws);
        orch.ensure_nx_package_jsons(ws);

        let content = std::fs::read_to_string(ws.join("libs/ui/package.json")).unwrap();
        assert_eq!(content, existing_pkg);
    }

    #[test]
    fn test_ensure_nx_package_jsons_skips_non_ts() {
        let tmp = tempfile::TempDir::new().unwrap();
        let ws = tmp.path();

        create_file(&ws.join("nx.json"), "{}");
        create_file(
            &ws.join("package.json"),
            r#"{"name": "root", "workspaces": ["apps/*"]}"#,
        );

        create_file(&ws.join("apps/backend/project.json"), "{}");
        create_file(&ws.join("apps/backend/main.go"), "package main");

        let orch = make_orchestrator(ws);
        orch.ensure_nx_package_jsons(ws);

        assert!(
            !ws.join("apps/backend/package.json").exists(),
            "package.json should NOT be generated for Go-only dir"
        );
    }

    #[test]
    fn test_ensure_nx_package_jsons_noop_without_nx() {
        let tmp = tempfile::TempDir::new().unwrap();
        let ws = tmp.path();

        create_file(
            &ws.join("package.json"),
            r#"{"name": "root", "workspaces": ["packages/*"]}"#,
        );
        create_file(&ws.join("packages/foo/project.json"), "{}");
        create_file(&ws.join("packages/foo/src/index.ts"), "export const x = 1;");

        let orch = make_orchestrator(ws);
        orch.ensure_nx_package_jsons(ws);

        assert!(
            !ws.join("packages/foo/package.json").exists(),
            "should be no-op without nx.json"
        );
    }

    #[test]
    fn test_rewrite_scip_document_paths() {
        let mut doc_bytes = Vec::new();
        IndexerOrchestrator::pb_write_varint(&mut doc_bytes, 10); // field 1, length-delimited
        IndexerOrchestrator::pb_write_varint(&mut doc_bytes, 7);
        doc_bytes.extend_from_slice(b"main.go");

        let mut index_bytes = Vec::new();
        IndexerOrchestrator::pb_write_varint(&mut index_bytes, 18); // field 2, length-delimited
        IndexerOrchestrator::pb_write_varint(&mut index_bytes, doc_bytes.len() as u64);
        index_bytes.extend_from_slice(&doc_bytes);

        let rewritten = IndexerOrchestrator::rewrite_index_document_paths(
            &index_bytes, "libs/common-go/"
        ).unwrap();

        let (tag, tag_end) = IndexerOrchestrator::pb_read_varint(&rewritten, 0).unwrap();
        assert_eq!(tag >> 3, 2);
        let (doc_len, doc_start) = IndexerOrchestrator::pb_read_varint(&rewritten, tag_end).unwrap();
        let new_doc = &rewritten[doc_start..doc_start + doc_len as usize];

        let (inner_tag, inner_tag_end) = IndexerOrchestrator::pb_read_varint(new_doc, 0).unwrap();
        assert_eq!(inner_tag >> 3, 1);
        let (path_len, path_start) = IndexerOrchestrator::pb_read_varint(new_doc, inner_tag_end).unwrap();
        let path = std::str::from_utf8(&new_doc[path_start..path_start + path_len as usize]).unwrap();
        assert_eq!(path, "libs/common-go/main.go");
    }

    #[test]
    fn test_rewrite_skips_dotdot_paths() {
        let mut doc_bytes = Vec::new();
        IndexerOrchestrator::pb_write_varint(&mut doc_bytes, 10);
        let old_path = b"../vendor/pkg.go";
        IndexerOrchestrator::pb_write_varint(&mut doc_bytes, old_path.len() as u64);
        doc_bytes.extend_from_slice(old_path);

        let result = IndexerOrchestrator::rewrite_document_path(&doc_bytes, "apps/cli/");

        let (_, tag_end) = IndexerOrchestrator::pb_read_varint(&result, 0).unwrap();
        let (path_len, path_start) = IndexerOrchestrator::pb_read_varint(&result, tag_end).unwrap();
        let path = std::str::from_utf8(&result[path_start..path_start + path_len as usize]).unwrap();
        assert_eq!(path, "../vendor/pkg.go", "paths starting with ../ should not be prefixed");
    }

    #[test]
    fn test_pb_varint_roundtrip() {
        for &value in &[0u64, 1, 127, 128, 300, 16384, 1_000_000, u64::MAX] {
            let mut buf = Vec::new();
            IndexerOrchestrator::pb_write_varint(&mut buf, value);
            let (decoded, _) = IndexerOrchestrator::pb_read_varint(&buf, 0).unwrap();
            assert_eq!(decoded, value, "varint roundtrip failed for {}", value);
        }
    }
}
