# System font verification

On 2026-10-26, Home Chooser was packaged without the 12 font files copied by
`@enact/moonstone` (3 Miso, 8 Museo Sans, and `Moonstone.ttf`). The unchanged
Moonstone CSS lists TV-local fonts before those URL fallbacks. `fc-list` on one
VM per selected webOS generation showed the following matches for those
`local()` names (family or PostScript name):

| Release | VM | LG Smart UI core and Condensed | LG Smart UI locale families | LG Smart UI JP | Moonstone | Miso | Museo Sans |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 5.x | w20l-jhericurl | present | present | present | present | present | present |
| 6.x | w21a-kisscurl | present | present | present | present | present | present |
| 9.2.1 | w22h-ombre | present | present | present | present | present | present |
| 10.x | w22o-ponytail-us | present | present | absent | present | present | present |
| 12.x | w24g-spacebuns | present | present | absent | present | present | present |

Core includes Regular, Light, SemiBold, and Bold aliases in both spaced family
and `LGSmartUI-...` forms. Condensed includes Regular, Light, SemiBold, and
Bold in both forms. Locale families covers the CSS names AR HE TH, Amharic,
Bengali, Devanagari, Dingbat, Gujarati, Gurmukhi, Kannada, Malayalam, Oriya,
TC, Tamil, and Telugu. The `LG Smart UI JP` family is absent on the two US
VMs noted above; their English chooser text uses the installed core family.

The pruned build was installed and opened on 6.x and 12.x. In both after
captures, the chooser text has the same LG UI appearance as the earlier
capture, and the selected LG Home row shows the home and checkmark glyphs
without missing-glyph boxes:

| Release | Earlier capture | Pruned build capture |
| --- | --- | --- |
| 6.x | [before](../../e2e/results/fonts-check-w21a-kisscurl/before.png) | [after](../../e2e/results/fonts-check-w21a-kisscurl/after.png) |
| 12.x | [before](../../e2e/results/fonts-check-w24g-spacebuns/before.png) | [after](../../e2e/results/fonts-check-w24g-spacebuns/after.png) |

The earlier captures were copied from
`e2e/results/w21a-kisscurl-20261024-213346/01-app-open.png` and
`e2e/results/w24g-spacebuns-20261025-223559/01-app-open.png`. On 12.x the
Homebrew Channel's `elevate-service` helper was run before the final capture,
as in the existing e2e runner, to show the normal selected row. The test app
was removed from both VMs after capture.

The existing IPK was 1,016,044 bytes before the packaging change. The rebuilt
IPK was 451,638 bytes, and listing `data.tar.gz` inside it found zero `.ttf`,
`.otf`, `.woff`, `.woff2`, or `.eot` files.
