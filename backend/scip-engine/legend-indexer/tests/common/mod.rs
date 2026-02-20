use legend_indexer::detect::{Language, LanguageInfo};
use std::fs;
use std::path::Path;

/// Skip a test if the given path does not exist (e.g. a repo not cloned locally).
macro_rules! skip_unless {
    ($path:expr) => {
        if !std::path::Path::new($path).exists() {
            eprintln!("SKIP: {} not found", $path);
            return;
        }
    };
}
pub(crate) use skip_unless;

pub fn create_file(dir: &Path, relative: &str, content: &str) {
    let path = dir.join(relative);
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).unwrap();
    }
    fs::write(&path, content).unwrap();
}

pub fn find_lang(langs: &[LanguageInfo], lang: Language) -> Option<&LanguageInfo> {
    langs.iter().find(|i| i.language == lang)
}
