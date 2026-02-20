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
                // Ensure gradle/maven wrappers are executable (git can strip +x on clone)
                self.fix_build_wrapper_permissions();
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

    /// Run scip-typescript indexer with two-phase strategy:
    /// Phase 1: Try directly from codebase path (works when mount is writable or tsconfig exists)
    /// Phase 2: Create a writable workspace with real tsconfig (avoids symlink overlay issues)
    fn run_typescript_indexer(&self, output: &Path) -> Result<()> {
        let output_str = output.to_str().unwrap();
        let has_root_tsconfig = self.codebase_path.join("tsconfig.json").exists();

        // Detect monorepo workspace type for scip-typescript flags.
        // Only pass --pnpm-workspaces if pnpm is actually installed (scip-typescript
        // calls `pnpm ls` which crashes hard if pnpm is missing).
        let workspace_flag = if self.codebase_path.join("pnpm-workspace.yaml").exists()
            && which::which("pnpm").is_ok()
        {
            Some("--pnpm-workspaces")
        } else if self.has_package_json_workspaces() {
            Some("--yarn-workspaces")
        } else {
            None
        };

        // Phase 1: Try directly from the real codebase path.
        // This avoids symlink overlays entirely (which crash Node.js with uv_cwd ENOENT
        // on Docker Desktop macOS/virtiofs).
        let mut args = vec!["index", "--output", output_str, "--max-file-byte-size", "10mb"];
        if !has_root_tsconfig {
            args.push("--infer-tsconfig");
        }
        if let Some(flag) = workspace_flag {
            args.push(flag);
        }

        let phase1 = self.try_typescript_from_dir(&args, &self.codebase_path);
        if phase1.is_ok() {
            return phase1;
        }

        // Phase 2: Direct indexing failed (likely read-only mount + --infer-tsconfig can't write).
        // Create a writable workspace with real tsconfig (preserves path aliases, jsx, etc.)
        warn!("Direct TypeScript indexing failed, retrying with writable workspace");
        let workspace = self.create_ts_workspace()?;
        let mut fallback_args = vec!["index", "--output", output_str, "--max-file-byte-size", "10mb"];
        if let Some(flag) = workspace_flag {
            fallback_args.push(flag);
        }
        self.try_typescript_from_dir(&fallback_args, &workspace)
    }

    /// Check if package.json has a "workspaces" field (yarn/npm monorepo)
    fn has_package_json_workspaces(&self) -> bool {
        let pkg_path = self.codebase_path.join("package.json");
        if !pkg_path.exists() {
            return false;
        }
        std::fs::read_to_string(&pkg_path)
            .ok()
            .and_then(|contents| serde_json::from_str::<serde_json::Value>(&contents).ok())
            .map(|v| v.get("workspaces").is_some())
            .unwrap_or(false)
    }

    /// Create a writable workspace for scip-typescript.
    /// If the codebase has a real tsconfig.json, copies it (preserving path aliases, jsx,
    /// moduleResolution, etc.). Only generates a minimal tsconfig for pure JS projects.
    /// Symlinks node_modules so module resolution works.
    fn create_ts_workspace(&self) -> Result<PathBuf> {
        let ws = self.output_dir.join("ts-workspace");
        if ws.exists() {
            std::fs::remove_dir_all(&ws)
                .context("Failed to clean previous ts-workspace")?;
        }
        std::fs::create_dir_all(&ws)
            .context("Failed to create ts-workspace")?;

        // Copy package.json if it exists (scip-typescript needs it for project detection)
        let pkg = self.codebase_path.join("package.json");
        if pkg.exists() {
            std::fs::copy(&pkg, ws.join("package.json"))
                .context("Failed to copy package.json to ts-workspace")?;
        }

        // Copy real tsconfig if it exists (preserves path aliases, jsx, moduleResolution, etc.)
        let tsconfig = self.codebase_path.join("tsconfig.json");
        if tsconfig.exists() {
            std::fs::copy(&tsconfig, ws.join("tsconfig.json"))
                .context("Failed to copy tsconfig.json to ts-workspace")?;

            // Also copy tsconfig.*.json files (base configs that tsconfig.json may extend)
            if let Ok(entries) = std::fs::read_dir(&self.codebase_path) {
                for entry in entries.flatten() {
                    let name = entry.file_name();
                    let name_str = name.to_string_lossy();
                    if name_str.starts_with("tsconfig.") && name_str.ends_with(".json")
                        && name_str != "tsconfig.json"
                    {
                        let _ = std::fs::copy(entry.path(), ws.join(&*name_str));
                    }
                }
            }
        } else {
            // No tsconfig exists — pure JS project, generate minimal one pointing to codebase
            let generated = format!(
                r#"{{"compilerOptions":{{"allowJs":true,"checkJs":false}},"include":["{}/**/*"]}}"#,
                self.codebase_path.display()
            );
            std::fs::write(ws.join("tsconfig.json"), generated)
                .context("Failed to write generated tsconfig.json")?;
        }

        // Symlink node_modules from codebase so module resolution works
        let node_modules = self.codebase_path.join("node_modules");
        if node_modules.exists() {
            #[cfg(unix)]
            {
                let _ = std::os::unix::fs::symlink(&node_modules, ws.join("node_modules"));
            }
        }

        info!("Created writable ts-workspace at {:?}", ws);
        Ok(ws)
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

    /// Ensure gradlew and mvnw are executable (git can strip +x bits on clone)
    fn fix_build_wrapper_permissions(&self) {
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            for wrapper in &["gradlew", "mvnw"] {
                let path = self.codebase_path.join(wrapper);
                if path.exists() {
                    if let Ok(meta) = path.metadata() {
                        let mut perms = meta.permissions();
                        perms.set_mode(perms.mode() | 0o111);
                        let _ = std::fs::set_permissions(&path, perms);
                    }
                }
            }
        }
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

    /// Pre-download Go module dependencies (Sourcegraph pattern: go mod download pre-step).
    /// Non-fatal on failure — graceful degradation.
    fn download_go_deps(&self, module_dir: &Path) {
        info!("Downloading Go module dependencies for {:?}...", module_dir);
        let status = Command::new("go")
            .current_dir(module_dir)
            .args(["mod", "download"])
            .status();
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
    fn run_go_indexer(&self, output: &Path) -> Result<()> {
        let output_str = output.to_str().unwrap();

        // If root has go.mod, download deps and run normally
        if self.codebase_path.join("go.mod").exists() {
            self.download_go_deps(&self.codebase_path);
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
        let mut any_success = false;
        for (i, dir) in go_mod_dirs.iter().enumerate() {
            // Pre-download deps for each module
            self.download_go_deps(dir);

            let sub_output = self.output_dir.join(format!("go-{}.scip", i));
            let sub_output_str = sub_output.to_str().unwrap();

            let binary = self.get_bundled_path("scip-go")
                .unwrap_or_else(|| PathBuf::from("scip-go"));
            let mut cmd = Command::new(&binary);
            cmd.current_dir(dir)
                .args(&["--output", sub_output_str]);
            if let Some(ref gowork_path) = gowork_env {
                cmd.env("GOWORK", gowork_path);
            }

            match cmd.status().with_context(|| format!("Failed to run scip-go in {:?}", dir)) {
                Ok(status) if status.success() => {
                    any_success = true;
                    // Use first successful output as the main output file
                    if !output.exists() {
                        std::fs::rename(&sub_output, output)
                            .context("Failed to rename Go sub-output")?;
                    }
                }
                Ok(status) => warn!("scip-go failed for {:?}: exit status {:?}", dir, status.code()),
                Err(e) => warn!("scip-go failed for {:?}: {}", dir, e),
            }
        }

        if any_success {
            Ok(())
        } else {
            Err(anyhow!("All Go module indexing failed"))
        }
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

    /// Find directories containing go.mod files (up to 3 levels deep)
    fn find_go_modules(&self) -> Vec<PathBuf> {
        let mut results = Vec::new();
        for entry in walkdir::WalkDir::new(&self.codebase_path)
            .max_depth(3)
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
        // If the codebase already has vendor/autoload.php, index directly
        if self.codebase_path.join("vendor/autoload.php").exists() {
            return self.run_indexer_and_move("scip-php", &[], &self.codebase_path, output);
        }

        // Check composer.json exists
        if !self.codebase_path.join("composer.json").exists() {
            return Err(anyhow!("No composer.json found in codebase"));
        }

        info!("Creating writable PHP workspace (vendor/ not found in codebase)");

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

            // Skip vendor (created via composer install), composer files (copied as real files),
            // and hidden dirs
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

        // Copy only composer.json (skip composer.lock — lock files often require specific
        // PHP versions that don't match the container; fresh resolve is more reliable)
        std::fs::copy(
            self.codebase_path.join("composer.json"),
            workspace.join("composer.json"),
        ).context("Failed to copy composer.json")?;

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

        // Run scip-php from the workspace (writes index.scip to cwd, then we move it)
        self.run_indexer_and_move("scip-php", &[], &workspace, output)
    }

    /// Run scip-clang indexer.
    /// Requires compile_commands.json to be present. Does NOT support --output;
    /// writes index.scip to cwd.
    fn run_clang_indexer(&self, output: &Path) -> Result<()> {
        // Find compile_commands.json (required by scip-clang)
        let compdb = self.codebase_path.join("compile_commands.json");
        if !compdb.exists() {
            // Check build/ subdirectory (CMake default)
            let build_compdb = self.codebase_path.join("build/compile_commands.json");
            if build_compdb.exists() {
                let compdb_str = build_compdb.to_string_lossy().to_string();
                let args_str = format!("--compdb-path={}", compdb_str);
                return self.run_indexer_and_move("scip-clang", &[&args_str], &self.codebase_path, output);
            }
            return Err(anyhow!(
                "compile_commands.json not found. Generate it with CMake (-DCMAKE_EXPORT_COMPILE_COMMANDS=ON), \
                 Bear (bear -- make), or Bazel"
            ));
        }

        self.run_indexer_and_move("scip-clang", &["--compdb-path=compile_commands.json"], &self.codebase_path, output)
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
        let status = Command::new("dart")
            .current_dir(&self.codebase_path)
            .args(["pub", "global", "run", "scip_dart", "."])
            .status()
            .context("Failed to run dart pub global run scip_dart")?;

        if !status.success() {
            return Err(anyhow!("scip-dart exited with status: {:?}", status.code()));
        }

        // Move index.scip from codebase dir to our output path
        let default_output = self.codebase_path.join("index.scip");
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
    fn run_indexer_and_move(&self, binary: &str, args: &[&str], working_dir: &Path, output: &Path) -> Result<()> {
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
            .current_dir(&self.codebase_path)
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
}
