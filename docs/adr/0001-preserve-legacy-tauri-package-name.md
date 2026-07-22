# Preserve the legacy Tauri package name by default

Tauri runtime support exposes `tauri_package_name` separately from the human-facing product name, but keeps `test-tauri-app` as its default so existing Copier answers regenerate the same internal identity. Projects opt into a project-specific package name explicitly because deriving it from a product name would make valid display names incompatible with npm, Cargo, or Rust naming rules.
