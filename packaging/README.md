# Linux 发行版打包

这里的两份配方安装 GitHub Release 里的 `guoapp-<版本>-linux-x64.tar.gz`（CI 在 Ubuntu 22.04 上构建，glibc 2.35+）。运行需要 `libmpv` 和 `ffmpeg`，deb / 发行版包会处理依赖。

当前版本 `2.0.4+116` 尚未发布时，下面的下载地址会 404；发布后按实际资产更新版本、tag 和校验值。

## Arch

`arch/PKGBUILD`（AUR 包名 `guoapp-bin`）：

- 发布后执行 `sha256sum guoapp-2.0.4+116-linux-x64.tar.gz`，把结果填入 `sha256sums`，替换 `SKIP`。
- `makepkg --printsrcinfo > .SRCINFO` 生成 AUR 用的元数据。
- 安装：`makepkg -si`。
- `options=(!strip)` 是必需的，Flutter 的 `libapp.so` 不能被 strip。

## Gentoo

`gentoo/media-video/guoapp-bin/` 可直接放进 overlay：

- 版本映射：上游 `2.0.4+116` → ebuild `2.0.4_p116`，tag `app-v2.0.4-116`。
- 发布后进入包目录执行 `ebuild guoapp-bin-2.0.4_p116.ebuild manifest` 生成 `Manifest`。
- 安装：`emerge --ask media-video/guoapp-bin`。
- `RESTRICT="strip"` 和 `QA_PREBUILT="*"` 用于预编译的 Flutter 包。

发布仓库与推送仓库不一致时，同时修改 PKGBUILD 的 `_repo`、ebuild 的 `HOMEPAGE` / `SRC_URI` 和 `metadata.xml` 的 `remote-id`。