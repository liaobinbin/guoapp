# Copyright 2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

MY_PV="2.0.4+116"
MY_TAG="app-v2.0.4-116"

DESCRIPTION="独立运行的多端短剧应用"
HOMEPAGE="https://github.com/liaobinbin/guoapp"
SRC_URI="https://github.com/liaobinbin/guoapp/releases/download/${MY_TAG}/guoapp-${MY_PV}-linux-x64.tar.gz -> ${P}.tar.gz"

LICENSE="GPL-3"
SLOT="0"
KEYWORDS="~amd64"
RESTRICT="strip"
QA_PREBUILT="*"

RDEPEND="
	x11-libs/gtk+:3
	media-video/mpv
	dev-libs/json-glib
	media-libs/libepoxy
	media-video/ffmpeg
"

S="${WORKDIR}"

src_install() {
	dodir /usr/lib/guoapp
	cp -a "${WORKDIR}/guoapp/." "${ED}/usr/lib/guoapp/" || die
	fowners -R root:root /usr/lib/guoapp
	fperms 0755 /usr/lib/guoapp/guoapp
	dosym ../lib/guoapp/guoapp /usr/bin/guoapp

	insinto /usr/share/applications
	newins "${WORKDIR}/guoapp.desktop" com.guoapp.app.desktop
	insinto /usr/share/icons/hicolor/512x512/apps
	newins "${WORKDIR}/guoapp.png" guoapp.png

	dodoc "${WORKDIR}/LICENSE"
}