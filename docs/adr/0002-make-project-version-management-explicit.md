# Make project version management explicit

Generated projects expose `use_version_management`, defaulting to `true`, and require it whenever selected runtime support needs maintained package or manifest version metadata. When disabled, the template omits the Version source, version availability checks, release automation, and their saved answers; incompatible configurations fail validation, while Copier's standard conditional update removes previously generated version-management files so existing versioned projects retain their current behavior by default.
