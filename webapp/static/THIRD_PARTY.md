# 포함한 외부 파일 (9.6 · 9.7 · 9.12)

시연 장비가 오프라인일 수 있으므로 글꼴·그래프 라이브러리를 앱에 포함한다(CDN 사용 안 함).
버전은 고정했고, 아래 주소에서 받은 파일 그대로다(2026-11-05).

| 구성요소 | 버전 | 받은 곳 | 파일 | 라이선스 |
| --- | --- | --- | --- | --- |
| Pretendard | 1.3.9 | `https://cdn.jsdelivr.net/npm/pretendard@1.3.9/dist/web/static/woff2/` | `fonts/pretendard/Pretendard-{Regular,Medium,SemiBold,Bold}.woff2` | SIL OFL 1.1 — `fonts/pretendard/LICENSE.txt` |
| JetBrains Mono (라틴 하위 집합) | @fontsource/jetbrains-mono 5.2.8 | `https://cdn.jsdelivr.net/npm/@fontsource/jetbrains-mono@5.2.8/files/` | `fonts/jetbrains-mono/jetbrains-mono-latin-{400,500,600}-normal.woff2` | SIL OFL 1.1 — `fonts/jetbrains-mono/LICENSE` |
| Chart.js (UMD) | 4.4.9 | `https://cdn.jsdelivr.net/npm/chart.js@4.4.9/dist/chart.umd.min.js` | `vendor/chartjs/chart.umd.min.js` | MIT — `vendor/chartjs/LICENSE.md` |

JetBrains Mono는 `frame_id`·숫자 표에만 쓰므로 라틴 하위 집합만 넣었다. 한글이 섞이면 Pretendard로 대체된다.
Pretendard는 굵기별 정적 파일(각 약 770 KB)이며 실제로 쓰는 굵기만 브라우저가 받는다.

## SHA-256

```
fad853f7f47c6c8b103171e7193fa095708cdcd70850a71d93aa5379e8a61d63  fonts/pretendard/Pretendard-Regular.woff2
d03481330eeba0659ab5b87f25ceb504a35de377dd90a0d0aba2982eb2d05e2c  fonts/pretendard/Pretendard-Medium.woff2
c863f76a7de5c1ddc1ed8b2fa794964530774592c4f31407a84e2a2ae93f17f0  fonts/pretendard/Pretendard-SemiBold.woff2
4609c3356e536fafe38f4add0daeceb3d8595d3057bce13c428c33ddbd43d362  fonts/pretendard/Pretendard-Bold.woff2
14425ba9c695763c1547f48a206b7aa60350a33ae23de09f0407877f3fcd89eb  fonts/jetbrains-mono/jetbrains-mono-latin-400-normal.woff2
cb182feeed4d798ff6961d3c79f7026279448fca0676438aaecb21f3fc39553a  fonts/jetbrains-mono/jetbrains-mono-latin-500-normal.woff2
400c6bfda18d5d14acad1c15d6dcb9f8e13c015e7286317e0b9a482539bef147  fonts/jetbrains-mono/jetbrains-mono-latin-600-normal.woff2
bce154080959c574be0bb6b1a924ff32f08ebc6ff460c159171f51c53802c844  vendor/chartjs/chart.umd.min.js
```

확인: `cd webapp/static && sha256sum -c <(sed -n '/^```$/,/^```$/p' THIRD_PARTY.md | grep -v '```')`
