# Vendor assets

Served by Rasova itself so a bill prints when the restaurant's internet is slow or gone, and so no outside host sits between a cashier and the page. Pinned versions; the SHA-256 of every file is listed so a swapped file shows up in a diff.

Fetched 11 November 2026. To update a library: download the new release's files to the same paths, update this list (`sha256sum`), bump the version in this file and in CHANGELOG.md.

| Library | Version | Source | Files |
|---|---|---|---|
| Bootstrap | 5.3.3 | https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/ | bootstrap/bootstrap.min.css, bootstrap/bootstrap.bundle.min.js |
| Bootstrap Icons | 1.11.3 | https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/ | bootstrap-icons/bootstrap-icons.min.css, bootstrap-icons/fonts/bootstrap-icons.woff2, .woff |
| SweetAlert2 | 11.10.8 | https://cdn.jsdelivr.net/npm/sweetalert2@11.10.8/dist/ | sweetalert2/sweetalert2.min.css, sweetalert2/sweetalert2.all.min.js |
| Chart.js | 4.4.1 | https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.js | chartjs/chart.umd.js (the reports dashboard) |
| Outfit (variable, 300 to 800), DM Serif Display (400, italic), Space Mono (400, 700, italics) | Google Fonts, 11 Nov 2026 | https://fonts.googleapis.com/css2?family=Outfit:wght@300..800&family=DM+Serif+Display:ital@0;1&family=Space+Mono:ital,wght@0,400;0,700;1,400;1,700 | fonts/fonts.css and 18 woff2 files (latin, latin-ext, vietnamese subsets) |

Licences: Bootstrap and SweetAlert2 are MIT; Bootstrap Icons is MIT; Outfit, DM Serif Display and Space Mono are under the SIL Open Font License 1.1.

The `sourceMappingURL` comment at the end of each minified file is removed: it names a `.map` file that is not shipped, and Django's manifest storage refuses to collect a file that references a missing one. The hashes below are of the files as served.

## SHA-256

```
d043aeea586f98e428879e9695f846345cadb03e7d9be4b1eacdfd95d1e6277e  bootstrap/bootstrap.bundle.min.js
08c201b05ee368a22f9d0958b0ae03da23cb1bfda011b6611c70366dc50b7d51  bootstrap/bootstrap.min.css
f643d6fe7e679f9de3e16311600c5ef5cd6b098f7a3a8828fcc29255d2b33e62  bootstrap-icons/bootstrap-icons.min.css
bb1de989b83970f6f4e54de1cd974c5cba55b73582da5e1b225a6d0edf029483  bootstrap-icons/fonts/bootstrap-icons.woff
476adf42b40325098fcfa8b36ab3e769186bb4f6ce6a249753e2e1a9c22bf99e  bootstrap-icons/fonts/bootstrap-icons.woff2
1cf6a486f532e8023f15f24f3b627417da02ba0cbb581c1ef1c92cad9162efb3  chartjs/chart.umd.js
e026521e59b7b3e5d3257cc76069916111683b97c24e75f0c7a9f07621aa9460  fonts/dm-serif-display-italic-400-latin-ext.woff2
ed8b291611e32fa2d2900488dd48fc95faf0957b2ef7ddf1527e8d21dd9cf2df  fonts/dm-serif-display-italic-400-latin.woff2
3af47815d6183efcee27a5717970acfe31153365e9b842e62d30c04e86c0613a  fonts/dm-serif-display-normal-400-latin-ext.woff2
fdf61e20fd2c0108e0ea28da4daca0035205b4b7fc031a3974b865b704de160a  fonts/dm-serif-display-normal-400-latin.woff2
e06da2e09fb2f1f91c35e03ffddcbf16cd225c0c10bcf0ba7655c885fbd18b20  fonts/fonts.css
0f53d1c03b3918d744a843b5039001ee31695ca1e255e3914188df81beb461e9  fonts/outfit-normal-300-800-latin-ext.woff2
6c18d579fd87c3776be068b762cbc83fde3acb543d49eabd3ade842eb987e887  fonts/outfit-normal-300-800-latin.woff2
a7642ae8cb7a389cb781e6c63d799e62932d916089afac98087f3af76b050a11  fonts/space-mono-italic-400-latin-ext.woff2
a5fb49042e7305918585561ab5fbd587d708ce74b0f54b56751e2b1151922d19  fonts/space-mono-italic-400-latin.woff2
98ef537999216c28a30d45f7b560539dd25ae06c65afd75829a3ce4c1cab59aa  fonts/space-mono-italic-400-vietnamese.woff2
0db253a66a34fcb80b80c5f8a400055550348e7c379e67349b07471d7b81a773  fonts/space-mono-italic-700-latin-ext.woff2
e84908e18ffcfab7d46490f4fd9781bc701cbd76aa3af4cb3e625858fd39ff3d  fonts/space-mono-italic-700-latin.woff2
4552826d453e9e439c836783704b1d5937a4bb8fc13a0740cab9970069596bf0  fonts/space-mono-italic-700-vietnamese.woff2
44ad50c760fa8f5b62da3d489edb40adebabaf97988b9b0adbbc3f0be859050a  fonts/space-mono-normal-400-latin-ext.woff2
fb4a81a2d0a893e5c38c394a7e716a1cef0b24610a0af49c96f6d529bd66bf2b  fonts/space-mono-normal-400-latin.woff2
a748973d5d192a27d69862f5af5f9182d4420d3a924e7b992ae82182e9b12cd8  fonts/space-mono-normal-400-vietnamese.woff2
52edd9a8959ec3e68d618e2a4757182c0e61bfeea61679850cad8744a68cadf3  fonts/space-mono-normal-700-latin-ext.woff2
2d46bd159b53f55c41167a4f1540a074649464194fd1e416f5b4694a6c0f282c  fonts/space-mono-normal-700-latin.woff2
97f6df0559c6393b851812484ae859b7c539815d5fcdab7d17234492480fa16c  fonts/space-mono-normal-700-vietnamese.woff2
ef7aceda0ec9484ac6f22b195c2b1edfd29fe721ae78f823cafa2dffc71108d4  sweetalert2/sweetalert2.all.min.js
8761a49fe1f7de59ca9504cd9ed4322d7316abbffd5c8dab94f0ac2ec55c501b  sweetalert2/sweetalert2.min.css
```
