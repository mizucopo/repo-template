# リポジトリテンプレート

新しいリポジトリをセットアップするためのシンプルなcopierテンプレート。

## 使い方

```bash
copier copy git@github.com:mizucopo/repo-template.git <destination>
```

## テンプレートの更新

このテンプレートの最新変更をプロジェクトに適用するには：

```bash
copier update
```

これにより、プロジェクト固有のカスタマイズを保持しながら、最新のテンプレート変更がマージされます。

既存プロジェクトで `.copier-answers.yml` の回答だけを変更し、同じテンプレートのバージョンに再適用する場合は `copier update` ではなく `copier recopy` を使用します。

```bash
copier recopy -f
```

例えば `use_gh_actions_release` を `false` から `true` に変更したあと、`.github/workflows/release.yml` を生成したい場合は `copier recopy -f` を実行してください。

## オプション

- `project_name`: 配布物・packageに使うkebab-caseのproject名
- `project_description`: projectの説明
- `project_version`: Python、Rust、runtimeなしprojectの初期SemVer version。Chrome Extension/Tauriのversion回答の既定値にも使う
- `use_python`: Python関連ファイルを生成するか
- `python_project_kind`: `application`、`package`、`library`のいずれか。`application`だけproject自身をinstallしない
- `python_package_name`: `package`または`library`で使うsnake_caseのimport package名
- `use_rust`: Rust関連ファイルを生成するか
- `use_chrome_extension`: Chrome Extension関連ファイルを生成するか
- `use_tauri`: Tauri関連ファイルを生成するか
- `tauri_package_name`: Tauri frontendとRust application shellで共有する内部package名（`use_tauri=true`の場合のみ、既定値は`test-tauri-app`）
- `tauri_product_name`: Window titleやbundle metadataに表示するTauriアプリ名（`use_tauri=true`の場合のみ）
- `tauri_identifier`: Tauri bundleの逆ドメイン形式identifier（`use_tauri=true`の場合のみ）
- `tauri_version`: TauriアプリのSemVer version（`use_tauri=true`の場合のみ）
- `use_docker`: Docker関連ファイルを生成するか
- `docker_registry`: Docker imageの配置先prefix（Docker Hubではimage namespace、Amazon ECRではregistry host。`use_docker=true`の場合のみ）
- `docker_login_username`: Docker Hubへ認証するusername（Docker Hub向けDocker releaseの場合のみ、既定値は`docker_registry`）
- `docker_image_name`: Docker imageのrepository名（`use_docker=true`の場合のみ）
- `use_dependabot_docker`: Docker imageをDependabotで監視するか（`use_docker=true`の場合のみ、既定値は`true`）
- `use_dependabot_github_actions`: GitHub ActionsをDependabotで監視するか（既定値はテンプレートがworkflowを生成する構成で`true`、それ以外で`false`）
- `use_gh_actions_docker_release`: .github/workflows/docker-release.ymlを生成するか
- `use_gh_actions_docker_quality`: pull requestでDocker build check、実build、任意のsmoke testを行う.github/workflows/docker-quality-checks.ymlを生成するか
- `dockerfile_path`: Docker quality workflowで使うDockerfileのrepository相対path
- `docker_build_context`: Docker quality workflowで使うbuild contextのrepository相対path
- `docker_smoke_command`: buildしたimage内で実行する最小smoke command。不要なら空文字
- `use_gh_actions_release`: .github/workflows/release.ymlを生成するか（`use_gh_actions_docker_release`が有効な場合は無視される）
- `use_gh_actions_chrome_extension_release`: Chrome Extension配布zip用の.github/workflows/chrome-extension-release.ymlを生成するか
- `chrome_extension_release_package_root_directory`: Chrome Extension配布release workflowが`npm ci`、quality gate、buildを実行するpackage root directory
- `chrome_extension_release_zip_name`: GitHub Releaseへ添付するChrome Extension配布zip名（`{version}`を`package.json`のversionに置換、path separatorと`#`は不可）
- `chrome_extension_release_title`: Chrome Extension配布用GitHub Release title（`{version}`をversionに置換）
- `chrome_extension_release_notes`: Chrome Extension配布用GitHub Release notes（`{version}`をversionに置換）
- `use_gh_actions_pr_tag_check`: .github/workflows/pr-tag-check.ymlを生成するか

### Docker build contextを安全に保つ

`use_docker=true` では、テンプレート管理の `.dockerignore` を生成します。既定値はrepository rootの全ファイルを除外し、生成されるDocker release workflowが使うroot `Dockerfile`だけをbuild contextへ含めるstrict allowlistです。そのため、Git履歴、`.env`やsecret、録画データなどのruntime outputは、個別に許可しない限りDocker builderへ送信されません。

Dockerfileの`COPY`や`ADD`に必要なproject fileは、親directoryと対象pathを `.dockerignore` の末尾で明示的に許可してください。例えば`src/`、`pyproject.toml`、`uv.lock`が必要な場合は次のように追加します。

```dockerignore
!src/
!src/**
!pyproject.toml
!uv.lock
```

入力を列挙できるprojectではstrict allowlistを維持してください。Dockerfileが多数の可変pathを必要とし、allowlistの維持が現実的でない場合だけdenylistへ変更し、少なくとも `.git/`、`.env*`、秘密鍵、credential、runtime outputを明示的に除外します。

`.dockerignore` はテンプレート管理対象です。既存projectへの初回適用では、`--pretend --overwrite`で置換内容を確認してから、`--overwrite`を指定してテンプレート標準へ移行してください。既にCopier管理されているprojectではcleanな専用branchで`copier update`を実行し、project固有のallowlist追加とテンプレート更新のmerge結果を確認します。履歴を使わず再生成する`copier recopy -f`はproject固有の変更を上書きし得るため、実行後のdiffで `.dockerignore` を必ず確認してください。

### Python applicationをDocker imageへ同期する

`use_python=true`では`python_project_kind`で構成を選びます。`application`は直接実行するmoduleを`src/`直下へ置き、`[tool.uv] package = false`としてproject自身をinstallしません。`package`と`library`は`src/<python_package_name>/`へimport packageを置き、Hatchlingでbuildできる`package = true`の構成を生成します。

Docker imageでも`src/` directoryを維持し、applicationの実行時に`src/`をworking directoryまたはPython pathとして指定します。例えば次の構成では依存関係を先に同期し、application codeを同じlayoutのまま追加できます。

```dockerfile
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen

COPY src ./src
CMD [".venv/bin/python", "src/app.py"]
```

既存projectへCopier updateを適用するときは、`pyproject.toml`とDockerfileを同じ変更としてreviewしてください。src-root application layoutでは`package = false`を採用し、Dockerfileに永続的な`--no-install-project`を追加して`package = true`との不一致を残さないでください。`package = false`では通常の`uv sync --frozen`がprojectをinstallしないため、`--no-install-project`は不要です。Dockerfileが`COPY src ./`でmoduleを`/app`直下へ展開している場合は、`COPY src ./src`へ変更し、起動commandのworking directoryまたはmodule pathも合わせて更新します。

再利用ライブラリは別のproject契約です。`package = true`と明示的なbuild systemを維持し、build backendが要求するpackage layout、`README.md`など`pyproject.toml`から参照するmetadata、package sourceをDocker build contextへ含めたうえで、最終imageでも通常の`uv sync --frozen`によってproject自身をinstallします。Docker layer cacheのために一時的に`uv sync --frozen --no-install-project`を使う場合も、sourceをcopyした後にprojectをinstallする最終`uv sync`が必要です。

移行後はlocalのquality gateに加え、実際のDocker buildと起動確認を行います。applicationは依存関係だけが同期され、再利用ライブラリはproject自身もinstallされることを、それぞれの契約として確認してください。

`use_chrome_extension=true` はManifest V3 TypeScript標準構成を生成します。starter sourceとstarter testは初回生成だけのproject所有ファイルで、Copier update/recopyでは既存内容を保持します。versionを`package.json`と同期する`src/manifest.json`、TypeScript/Vitest/ESLint/Prettier設定、build script、Chrome Extension quality workflowはテンプレート管理対象です。

### 既存Chrome Extensionを標準構成へ移行する

cleanな専用branchで`copier update`を実行します。まだCopier管理されていないprojectへ初回適用するときは、先に`copier copy --trust --defaults --overwrite --pretend`で差分を確認してください。既存のstarter sourceとstarter testは`_skip_if_exists`で保持されます。manifestはversion同期のためテンプレート管理対象なので、project固有のpermissionsなどがある場合はCopierのmerge結果を確認します。適用後は`git diff`、`npm install`、`npm run check`を実行し、lockfileを含む差分をreviewします。

同じtemplate versionの回答だけを再適用する場合は`copier recopy -f`を使います。新しいtemplate versionを取り込む場合は`copier update`を使い、どちらも専用branchのcleanな作業ツリーで実行します。

旧 `chrome_extension_mode`、`adopt_existing`、`javascript_rollup`、`chrome_extension_manifest_path` は廃止しました。古い `.copier-answers.yml` にこれらの回答が残っている場合も、再適用後は削除されます。starter sourceとstarter testはproject所有として保持し、manifestは回答値との同期対象として更新します。

`use_tauri` は専用の `src-tauri` と Node.js フロントエンドを生成するため、`use_rust` と `use_chrome_extension` とは同時に利用できません。

### Tauriの表示名とpackage名を分ける

`tauri_product_name` はwindow titleなどの表示名で、空白を含む値を指定できます。`tauri_package_name` はnpm、Cargo、Rustで共有する内部identityであり、最大64文字のlowercase kebab-caseを指定します。例えば `tauri_product_name=Mizu Pairrank` と `tauri_package_name=mizu-pairrank` は、次の名前へ生成されます。

- `package.json`の`name`: `mizu-pairrank`
- `src-tauri/Cargo.toml`のpackage名: `mizu-pairrank`
- `src-tauri/Cargo.toml`のRust lib名: `mizu_pairrank_lib`
- `src-tauri/src/main.rs`のlib参照: `mizu_pairrank_lib::run()`

既存の`.copier-answers.yml`に`tauri_package_name`がない場合、既定値`test-tauri-app`を使うため、従来の内部identityは変わりません。project固有の名前へ移行するには、`.copier-answers.yml`へ次の回答を追加または変更し、同じtemplate revisionへ再生成します。

```yaml
tauri_package_name: mizu-pairrank
```

```bash
copier recopy -f
npm run check
```

再生成後は`package.json`、`src-tauri/Cargo.toml`、`src-tauri/src/main.rs`の差分が同じidentityへ揃っていることを確認してください。

## 生成される主なファイル

Copierの回答に応じて、以下のようなファイルが生成されます。条件の詳細はオプションの組み合わせで決まるため、ここでは生成後に確認する主なファイルの役割を説明します。

### 共通ファイル

- `.copier-answers.yml`: Copierの回答を記録するファイル。`copier update` や `copier recopy` はこの内容をもとにテンプレートを再適用します。
- `.gitignore`: 選択したruntime supportに応じて、生成物やlocal環境ファイルをGit管理から除外します。
- `.dockerignore`: `use_docker=true`の場合に、Docker build contextを必要な入力だけへ限定するstrict allowlistを生成します。
- `AGENTS.md`, `CLAUDE.md`: 生成先リポジトリで作業するエージェント向けの共通ルールと、選択したruntime supportごとの品質確認手順をまとめます。
- `docs/agents/issue-tracker.md`: GitHub Issuesを追跡先として扱う共通規約と、Git remoteから対象repositoryを判断するルールをまとめます。
- `docs/agents/triage-labels.md`: agent skillが使う標準5種のtriage roleとGitHub labelの対応を定義します。
- `docs/agents/domain.md`: root `CONTEXT.md`と`docs/adr/`を参照する単一contextのdomain docs導線を定義します。
- `LICENSE`: MITライセンスを選択した場合に生成されます。
- `version`: Python、Rust、Chrome Extension、Tauriのruntime supportを使わない場合に、release workflowのversion sourceとして生成されます。

### 既存のagent workflow guidanceを移行する

既存の生成先へ更新すると、`AGENTS.md`、`CLAUDE.md`に`Agent skills`セクションが加わり、`docs/agents/`の3文書がテンプレート管理対象になります。cleanな専用branchで`copier update`を実行し、既存の同名セクションや文書とのmerge結果を確認してください。

標準のGitHub Issues、5種のtriage label、単一context構成を使うrepositoryでは、生成内容をそのまま採用できます。明示的なrepository名、`.scratch/`の扱い、独自label mapping、複数contextの参照先などは生成先固有の差分として3文書と両ガイダンスへ反映し、`AGENTS.md`と`CLAUDE.md`の内容は一致させてください。これらの差分はCopierの更新時にreviewし、テンプレート共通ルールへ固定しません。

### Dependabot更新

`.github/dependabot.yml` は、選択したruntime support、Docker Dependabot monitoring、GitHub Actions Dependabot monitoringから自動生成されます。複数の条件に該当する場合は、対応するすべてのecosystemを `updates` に含めます。

| 生成条件 | package ecosystem | directory |
| --- | --- | --- |
| `use_python=true` | `uv` | `/` |
| `use_rust=true` | `cargo` | `/` |
| `use_tauri=true` | `cargo` | `/src-tauri` |
| `use_chrome_extension=true` または `use_tauri=true` | `npm` | `/` |
| Chrome Extension配布releaseのpackage rootが`.`以外 | `npm` | `/` + package root |
| `use_docker=true` かつ `use_dependabot_docker=true` | `docker` | `/` |
| `use_dependabot_github_actions=true` | `github-actions` | `/` |

更新確認はすべて週次です。通常の依存関係はminor / patch更新を `minor-and-patch` に、GitHub Actionsはすべての更新を `github-actions` にまとめます。自動mergeは設定せず、生成先の通常のreviewとCIを経てmergeします。監視対象のecosystemがない構成では `.github/dependabot.yml` を生成しません。

`use_dependabot_github_actions` は、テンプレート標準のworkflowと生成先固有のworkflowを同じGitHub Actions Dependabot monitoringとして扱います。既定値は後方互換性のため、テンプレートがworkflowを1つ以上生成する構成では`true`、生成しない構成では`false`です。テンプレート標準workflowを無効にして独自workflowだけを管理する場合は、`use_dependabot_github_actions=true`を明示してください。逆に、テンプレート標準workflowがあってもGitHub Actionsの更新を監視しない場合は`false`を選択できます。

既存プロジェクトではcleanな専用branchで`copier update`を実行し、現在のworkflow生成条件から提示される既定値を確認してください。選択した値は`.copier-answers.yml`へ保存され、以後の`copier update`と`copier recopy`で再利用されます。GitHub Actionsを含む全ecosystemを無効にしたときは設定自体が生成対象外になりますが、Copierは既存の条件付き生成ファイルを自動削除しないため、初回だけ`.github/dependabot.yml`を手動で削除してください。

Docker Dependabot monitoringは、Dockerfileのliteralな `FROM` imageをDependabotが更新できる構成を前提とします。GitHubの公式ドキュメントにあるとおり、`ARG` で指定したDocker imageは更新対象になりません。このような構成では `use_docker=true` のまま `use_dependabot_docker=false` にすると、Docker関連ファイルを維持しつつDocker ecosystemだけを除外できます。他のecosystemがあればその設定を残し、なければ `.github/dependabot.yml` 自体を生成しません。

既存プロジェクトでopt-outする場合は `.copier-answers.yml` の `use_dependabot_docker` を `false` に変更し、`copier recopy -f` を実行してください。他のecosystemが残る場合は既存の `.github/dependabot.yml` からDocker ecosystemが除かれます。Docker ecosystemしかない場合、Copierは生成対象外になった既存ファイルを自動削除しないため、初回だけ `.github/dependabot.yml` も削除してから再適用してください。以後の `copier update` と `copier recopy` では回答値が再利用され、Docker ecosystemは再追加されません。制約の詳細は[GitHub公式ドキュメント](https://docs.github.com/en/enterprise-cloud@latest/code-security/how-tos/secure-your-supply-chain/manage-your-dependency-security/configure-private-registries#docker)を参照してください。

### Python関連ファイル

- `.python-version`: 生成先リポジトリで使うPythonバージョンを固定します。
- `pyproject.toml`: Pythonプロジェクトのメタデータ、依存関係、ruff、mypy、pytestなどの設定をまとめます。
- `src/`, `stubs/`, `tests/`: Python実装、型スタブ、テストの初期ディレクトリです。starter fileは初回生成後にproject所有となります。
- `.github/workflows/pr-quality-checks.yml`: pull requestでpytest、mypy、ruffを実行し、結果をActions summaryに集約します。

### Rust関連ファイル

- `Cargo.toml`: root Rust runtime supportのCargo package定義です。
- `rust-toolchain.toml`: Rust toolchainを固定し、local環境とCIの差を抑えます。
- `src/main.rs`: root Rust runtime supportの最小実行ファイルです。初回生成後はproject所有となります。
- `.github/workflows/rust-quality-checks.yml`: `cargo fmt`、Clippy、Cargo testを実行するquality gateです。

### Chrome Extension関連ファイル

- `.node-version`: Chrome ExtensionまたはTauriで使うNode.jsバージョンを固定します。
- `package.json`: TypeScript build、Vitest、ESLint、Prettier scriptなどをまとめます。
- `src/manifest.json`: Chrome Extensionの名前、説明、versionを回答値と同期するテンプレート管理対象です。
- `src/background.ts`, `src/popup.html`, `src/popup.ts`, `src/popup.css`: Manifest V3 Chrome Extensionのstarter実装です。初回生成後はproject所有となります。
- `src/lib/`, `tests/`: 再利用するTypeScriptロジックとVitestテストを置く初期ディレクトリです。
- `scripts/copy-extension-assets.mjs`, `scripts/clean-dist.mjs`: Chrome拡張のbuild outputを整える補助scriptです。
- `tsconfig.json`, `tsconfig.build.json`, `eslint.config.mjs`, `vitest.config.ts`, `.prettierrc.json`, `.prettierignore`: TypeScript、lint、test、formatの設定です。
- `.github/workflows/chrome-extension-quality-checks.yml`: lint、format、typecheck、Vitest、buildを実行するquality gateです。

### Tauri関連ファイル

- `package.json`, `.node-version`, `index.html`, `vite.config.ts`: Tauri frontendのNode.js/Vite設定とentrypointです。
- `src/main.ts`, `src/styles.css`, `src/lib/greeting.ts`, `tests/lib/greeting.test.ts`: TypeScript frontendのstarter実装とテストです。初回生成後はproject所有となります。
- `src-tauri/Cargo.toml`, `src-tauri/src/`, `src-tauri/tauri.conf.json`, `src-tauri/capabilities/default.json`, `src-tauri/build.rs`: Tauri application shell、Rust code、権限、bundle設定をまとめます。
- `src-tauri/icons/`: Tauri bundleで使う初期iconです。
- `rust-toolchain.toml`: Tauri側のRust toolchainを固定します。
- `.github/workflows/tauri-quality-checks.yml`: frontendのlint、format、typecheck、test、buildと、Rust側のrustfmt、Clippy、Cargo testを実行するquality gateです。

Python、Rust、Chrome Extension、Tauri、DockerのPR quality workflowは必須quality gateです。独立した品質checkは、1つが失敗しても残りを実行し、結果をActions summaryへ集約します。1つでも非成功ならworkflow job自体を`failure`にするため、setup、依存関係の導入、summary作成を含む失敗が成功扱いになることはありません。

生成先のmain保護Rulesetでは、利用する技術に対応するnative job名をrequired status checkへ登録します。存在しない技術のjobは生成も登録もしません。

- Python: `quality-checks`
- Rust: `rust-quality-checks`
- Chrome Extension: `chrome-extension-quality-checks`
- Tauri: `tauri-quality-checks`
- Docker: `docker-quality-checks`

テンプレート自身は`.github/workflows/template-quality-checks.yml`の`template-quality-checks` jobで全render/behavior testを実行します。生成されるGitHub Actions参照はreview済みのfull commit SHAへ固定し、行末コメントでrelease versionを示します。

### Release関連ファイル

- `.github/workflows/release.yml`: version sourceを読み、git tagとGitHub Releaseを作成します。
- `.github/workflows/chrome-extension-release.yml`: Chrome Extension配布zipを作成し、git tagとGitHub Releaseに添付します。
- `.github/workflows/docker-release.yml`: Docker imageをbuild/pushし、git tagとGitHub Releaseを作成します。
- `.github/workflows/pr-tag-check.yml`: pull request上でRelease version availabilityを確認します。

PR tag checkは、version sourceを読み取り、同名のgit tagとGitHub Releaseがどちらも存在しないことを明示的に確認できた場合だけ成功します。Docker releaseが有効な構成では、configured image registry（Docker HubまたはAmazon ECR）のversioned image tagも存在しないことを確認します。複数の衝突がある場合はsummaryへすべて列挙し、versionの読取失敗、各状態の確認失敗、または1つ以上の既存状態を、公開する`Version Tag Check`とnative `check-tag-conflict` jobの両方でfailureにします。独自Check Runの公開に失敗した場合も、native jobがRelease version availabilityを独立して強制します。

GitHub ReleaseとDocker Hubの照会はHTTP 200だけを存在、404だけを未作成として扱い、その他のstatusや通信失敗では安全側に失敗します。公開Docker Hubリポジトリのタグは匿名APIで照会するため、secretを利用できないpull requestでも確認できます。ECRでは同じ`AWS_ROLE_ARN`をOIDCで引き受けて、`ImageNotFound`だけを未作成として扱います。registryが検証可能な状態を返さない場合は成功扱いにしません。

`docker_registry`はDocker Hubではimage namespace、Amazon ECRでは`aws_account_id.dkr.ecr.aws_region.amazonaws.com`形式のregistry hostとして、imageのpush先とpull例に使います。

Docker Hub向けのDocker releaseでは、`docker_login_username`を`DOCKERHUB_TOKEN`に対応するlogin usernameとして使います。個人namespaceへ本人のtokenでpushする単純な構成では、`docker_login_username`の既定値が`docker_registry`と同じになるため追加設定は不要です。organization namespaceへservice accountでpushする場合は、namespaceを`docker_registry`、service account名を`docker_login_username`へ別々に設定してください。imageのpush先と公開URLは常に`docker_registry/docker_image_name`のままです。

PR tag checkとgeneric / Docker release workflowは同じValidated release versionの契約を使います。version sourceの値は単一行・非空・許可されたrelease tag文字・有効なGit refであることを確認し、Docker releaseではDocker tagの文字と128文字上限も確認します。検証済みの値だけをstep outputへ書き、後続のshellではenvironment variableとして引用して扱います。

生成されるrelease workflowはRerunnable releaseです。mainへのpushごとにcommit SHAで独立したrunを保持し、後続pushで待機中のreleaseを置き換えません。手動実行もmain以外のrefでは停止します。各runは永続化済みの状態を確認して不足工程だけを再開します。GitHub Releaseの照会はHTTP 200だけを存在、404だけを未作成として扱います。同名tagが別commitを指す場合、GitHub Releaseだけが存在する場合、API・認証・通信に失敗した場合は、既存状態を未作成とみなさず安全側に失敗します。

generic release workflowは、現在のrelease commitを指すtagとGitHub Releaseが揃った状態を完了とし、再実行ではno-opになります。同一commitのtagだけが存在する場合は不足しているGitHub Releaseだけを作成します。

Docker release workflowは、現在のrelease commitを指すtag、GitHub Release、versioned image、versioned imageと同じdigestの`latest`が揃った状態を完了とします。Docker HubとAmazon ECRの両方でimage状態をAPIから確認し、同一commitのtagがある状態でversioned imageがなければ両tagをbuild/pushし、versioned imageがあって`latest`がないかdigestが異なる場合はversioned manifestから`latest`だけを修復します。現versionのimageだけが存在して対応するgit tagがない場合や、存在するimageのdigestを検証できない場合は、そのimageを現在のcommitへ誤関連付けしないよう安全側に失敗します。

Chrome Extension distribution release workflowは、現在のrelease commitを指すtag、GitHub Release、設定された名前のDistribution ZIP assetが揃った状態を完了とします。assetが存在する再実行では依存関係のinstall、quality gate、build、zip作成、uploadを行いません。assetが不足している場合だけDistribution ZIPを再生成してuploadし、既存assetを上書きしません。

`use_gh_actions_chrome_extension_release=true` はChrome Extension runtime support専用の配布release workflowです。write権限でtagとGitHub Releaseを作成できるように `main` へのpushで起動し、checkoutしたcommitが `main` 向けにmerge済みのpull request由来であることを検証します。そのうえで `package.json` とChrome manifestのversion一致、Chrome manifest version形式、既存tagが別commitを指していないことを確認し、必要な場合だけ`npm ci`、生成先プロジェクトの `npm run check`、`npm run build`、配布zip作成、tag作成、GitHub Release作成、zip uploadまでを実行します。build後に `dist/manifest.json` がある場合は `dist` を配布zipのrootにし、ない場合は設定されたChrome manifestがあるdirectoryを配布zipのrootにします。実際にzipする `manifest.json` のversionもrelease直前に再検証します。

Chrome Extension配布release workflowを使う生成先プロジェクトでは、`chrome_extension_release_package_root_directory` に `package.json` があるdirectoryを指定してください。Node.js versionはテンプレートがrepository rootに生成する `.node-version` を使います。workflowはlockfileを前提に `npm ci` を実行するため、生成先プロジェクトでは `package-lock.json` をcommitしておく必要があります。配布zip名、GitHub Release title、release notesはtemplate answersから生成され、`{version}` placeholderはrelease時の `package.json` versionに置換されます。`use_gh_actions_pr_tag_check=true` も併用する場合、Chrome ExtensionのPR tag checkは同じpackage root directoryの `package.json` をversion sourceとして検証します。

既存の `use_gh_actions_release=true` はversion sourceからtagとGitHub Releaseだけを作成する汎用release workflowです。Chrome Extensionの配布zipをRelease assetとして添付したい場合は `use_gh_actions_chrome_extension_release=true` を使い、tag-onlyの汎用releaseが必要な場合だけ `use_gh_actions_release=true` を使ってください。同じversion tagを作成するため、Chrome Extension配布release workflowは `use_gh_actions_release=true` や `use_gh_actions_docker_release=true` と同時に有効化できません。

Chrome Extensionを使う場合、release workflowのversion sourceは `package.json` の `version` です。PR上の `.github/workflows/pr-tag-check.yml` は、`package.json` と `src/manifest.json` の `version` を両方読み、Chrome manifest version形式と両者の一致をmerge前に検証します。不一致や不正なmanifest versionは、tag確認前に明確な失敗checkとして表示され、workflowも失敗します。

## ライセンス

詳細は[LICENSE](LICENSE)を参照してください。
