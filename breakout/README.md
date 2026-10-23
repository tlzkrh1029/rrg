# 돌파 지도 (BKM)

28개 ETF 의 구성 비중 상위 10개 종목을 칸으로 놓고, 칸마다 최근 30일 종가 선을 그리며, 종가가 지평별 고점(Shelf)을 처음 넘은 칸을 켜는 화면입니다. 섹터 전환 매트릭스(`terminal/`)의 23개 ETF 에 GDX·GRID·XOP·TAN·JETS 를 더한 것이 블록이고, 같은 ETF 를 매트릭스의 행과 이어 보도록 만들었습니다.

- Pages: `https://tlzkrh1029.github.io/rrg/breakout/` (매트릭스의 `3) 돌파 지도` 탭, 돌파 지도의 사이트 줄 `1) 매트릭스`)
- Claude 아티팩트: `https://claude.ai/artifact/AMAB2TMoSxwXPuLPWjtjGB` (`docs/breakout/breakout.artifact.html` 을 발행)

## 흐름

```
fetch_prices.py (루트)        ETF 가격 → output/prices_wide.csv (SPY 축), 구성종목 → output/holdings/<날짜>.csv
breakout/fetch_stock_prices.py   구성종목 일봉(2년) → output/stocks/prices_wide.csv, ath.json, summary.json
breakout/compute_breakout.py     90일 창·기준 수준·판정 → output/breakout-data.json   (--lists 로 상장 점)
breakout/build_breakout.py       템플릿에 끼움 → docs/breakout/index.html, breakout.artifact.html, data/{breakout-data,alerts}.json, alerts.txt, brief.txt
```

GitHub Actions(`.github/workflows/terminal.yml`)가 화~토 아침에 수집·계산을 매트릭스보다 먼저 실행하고(매트릭스의 종목 상세가 `output/breakout-data.json` 을 끼워 넣기 때문), 페이지 생성은 매트릭스 다음에 합니다. 돌파 지도 단계가 실패해도 매트릭스 빌드와 커밋은 막지 않습니다. 주소 끝에 `#NVDA` 처럼 티커나 ETF 를 붙이면 그 칸·블록으로 바로 갑니다. 손으로 돌릴 때는 루트에서 위 순서대로 실행하면 되고, 모두 표준 라이브러리만 씁니다.

```
python3 fetch_prices.py
python3 breakout/fetch_stock_prices.py            # 약 240 종목, 처음에는 전체 이력도 받아 5~10분
python3 breakout/compute_breakout.py --lists data/lists
python3 breakout/build_breakout.py
```

## 파일

| 파일 | 역할 |
|---|---|
| `fetch_stock_prices.py` | 최신 구성종목 스냅샷에서 `data/etf_meta.json` 의 `screens` 에 `breakout` 이 든 ETF 의 상위 10개 종목을 모아 Yahoo chart(2y)에서 조정 종가를 받고(미국 종목은 Stooq 를 2순위로), SPY 축에 맞춰 `output/stocks/prices_wide.csv` 를 씁니다. 비미국 종목의 현지 휴일은 직전 종가로 채웁니다. 처음 보는 종목은 전체 이력(range=max)을 한 번 받아 "2년 창 이전의 최고 종가"를 `output/stocks/ath.json` 에 캐시하고, 30일이 지났거나 기준일 종가가 2% 넘게 달라지면(액면분할·배당 재조정) 다시 받습니다. 종료 코드 0 정상, 1 축 없음, 2 일부 실패(`summary.json` 의 `failed`·`warnings`). |
| `match_listing.py` | `tradfi-ticker-lists` 의 세 목록(바이낸스 bStocks, 바이낸스 TradFi 선물, 하이퍼리퀴드 HIP-3)을 종목·ETF 에 대조해 거래소 원본 심볼까지 모읍니다. 미국 종목은 티커로, 비미국 종목은 `거래소:코드`(KRX:005930, HKEX:0100)로, 미국에도 상장된 비미국 표기는 `DUAL` 표로 미국 티커까지 찾습니다. 단독 실행하면 `output/stocks/listing.json` 과 ETF 별 집계를 출력합니다. |
| `compute_breakout.py` | 종목마다 마지막 90거래일 종가(`c`)와 그 앞 160거래일 종가(`cx`, 종목 상세의 50 SMA·89 HMA·144 HMA 용; `spyx`·`qqqx` 는 같은 구간의 지수), 창 이전 52주 고점(`h52`: 창 앞 192거래일의 최고, 판정 창 첫날 기준 252일), 창 이전 사상 최고(`ath`: 캐시 + 2년 창), SPY·QQQ 비율의 같은 수준(`rspy`, `rqqq`)을 만들고, 세 기준 × 네 지평의 판정을 파이썬으로도 내려 `judge` 와 요약 `stats` 를 씁니다. 페이지의 JS 판정과 같은 규칙이며 2,892개 칸·기준·지평 조합에서 결과가 일치하는 것을 확인했습니다. |
| `build_breakout.py` | `template.html` 의 토큰을 채워 두 본문을 만듭니다. Pages 본문의 매트릭스 링크는 `../#board`, 아티팩트 본문은 매트릭스 아티팩트를 새 탭으로 엽니다. 직전 빌드(`docs/breakout/data/breakout-data.json`)와 비교해 알림을 만듭니다. |
| `template.html` | 화면. `__DATA__`, `__LISTING__`, `__PRICE_ASOF__`, `__TERMINAL_URL__` 같은 토큰을 빌드가 채웁니다. 화면을 고칠 때는 이 파일을 고칩니다. |

`output/breakout-data.json` 의 `stocks[key]` 는 `n`(이름), `x`(거래소 접두어), `sym`(Yahoo 심볼), `src`, `c`, `h52`, `ath`, `athFrom`(이력 시작일), `rspy`, `rqqq`, `ipo`(90일 창을 다 채우지 못한 신규 상장 종목만)이고, `etfs[]` 는 메타와 `rows`(칸이 있는 종목), `rows_all`(표의 전체 행; 칸이 없는 행은 `swap`·`nosym`·`nodata` 표시), `c`(ETF 자체 종가 90일)입니다. 페이지에는 `judge`·`stats`·`listing` 을 뺀 나머지를 `DATA` 로, `listing` 을 `LISTING` 으로 넣습니다.

## 판정 규칙

상수는 `compute_breakout.py` 와 `template.html` 에 같은 값으로 있습니다: `W=30`(판정 창), `GAP=5`, `MARGIN=1.005`, `TOL=0.99`, `MAXAGE=10`, `FAILWIN=5`, `NEAR=0.01`.

| 용어 | 정의 |
|---|---|
| Shelf | 지평 N일(20일·55일) 동안의 최고 종가에서 최근 5일을 뺀 값. 52주·사상 최고 지평은 창 이전 수준(`h52`/`ath`)과 창 안의 종가를 함께 봅니다. 최근 5일을 빼는 이유는 오르는 중인 종목이 어제의 고점을 오늘 조금 더 넘는 것을 돌파로 세지 않기 위해서입니다. |
| 돌파 | 직전 5일의 종가가 모두 Shelf 의 100.5% 이하였다가 오늘 종가가 그 위로 처음 올라선 날(경과 1일). |
| 켜짐 | 돌파 후 10일 이내이고 종가가 아직 Shelf 의 99% 위. 날이 갈수록 채움이 옅어집니다. 52주 고점(또는 사상 최고)까지 넘은 칸은 경과일 표가 진한 초록입니다. |
| 실패 | 돌파 후 5일 안에 종가가 Shelf 의 99% 아래로 되돌아온 칸. 되돌아온 뒤 5일 동안 빨간 점선 테두리. |
| 이탈 | 돌파의 거울. 바닥(최근 5일을 뺀 N일 최저; 52주·사상 최고 지평은 55일)의 99.5% 아래로 처음 내려선 날이 1일이고, 10일 이내에 아직 바닥의 101% 아래이면 빨간색. |
| 직전 | 돌파도 이탈도 아니지만 종가가 천장(지평 고점)의 1% 안. 파란 점. |
| 유지 | 돌파한 지 10일이 지났지만 아직 Shelf 위. 색은 빼고 Shelf 만 실선으로. |

세 탭은 같은 판정을 다른 자로 잽니다. `vs. USD` 는 절대 가격, `vs. QQQ`·`vs. SPY` 는 같은 날의 종가를 그 지수로 나눈 비율입니다. 비율의 "사상 최고" 는 전체 이력이 없으므로 2년 창 안의 최고 비율입니다.

## 데이터와 한계

- **가격**: Yahoo chart 의 배당·분할 조정 종가입니다. 비미국 종목(KRX, TWSE, TSE, SSE, HKG, TSX, LON, ASX, JSE …)은 현지 거래소 종가를 현지 통화 그대로 쓰므로 환율 변동은 들어 있지 않습니다. 거래소 접두어 → Yahoo 접미어 표는 `fetch_stock_prices.py` 의 `YAHOO_SUFFIX` 에 있고, 코스닥·TWSE 상장 여부를 모르는 종목은 `.KS`→`.KQ`, `.TW`→`.TWO` 순으로 시도합니다. 심볼이 빈 행(URA 의 Sprott Physical Uranium Trust)은 칸 없이 표에만 남습니다.
- **종가 시점**: 아시아 종목은 미국 장 마감보다 먼저 끝나므로 같은 "날짜" 라도 더 이른 시점의 종가입니다. 미국 휴장일이 아닌 현지 휴일에는 직전 종가를 둡니다.
- **사상 최고 캐시**(`output/stocks/ath.json`): 조정 종가는 배당 때마다 과거 값이 다시 계산되므로 캐시는 30일마다, 그리고 기준일 종가가 2% 넘게 달라졌을 때 다시 받습니다. 전체 이력을 받지 못한 종목은 2년 창만으로 사상 최고를 잡고 `summary.json` 의 `warnings` 에 적습니다.
- **신규 상장**: 상장 45거래일 미만이면 칸을 만들지 않고, 90일 창을 다 채우지 못하면 상장 전 구간을 첫 종가로 채우고 패널에 상장일을 적습니다. 이런 종목의 52주·사상 최고 지평은 "상장 이후 최고" 입니다.
- **구성종목**: stockanalysis.com 의 ETF 구성종목 표(상위 10개)이며 DRAM·MAGS 는 Roundhill 일일 보유 파일에서 스왑 노출을 기초 종목에 합산한 값입니다. 비중은 페이지 갱신 시점의 값입니다. 같은 종목이 여러 ETF 에 있으면(NVDA 는 XLK 와 SMH) 두 블록에 모두 나오고 같은 가격을 씁니다.
- **상장 점**: `tradfi-ticker-lists` 의 목록으로, Actions 가 목록을 새로 받지 못하면 `data/lists/` 의 마지막 사본 날짜에 멈춥니다. 속 빈 주황 = 바이낸스 bStocks, 속 찬 주황 = 바이낸스 TradFi 선물, 속 찬 하늘색 = 하이퍼리퀴드 HIP-3.
- **수집 시간**: 약 240 종목을 0.2초 간격으로 받으면 2~3분, 처음 실행이나 캐시 갱신일에는 전체 이력까지 받아 5~10분입니다. Yahoo 가 429 를 돌려주면 15초 쉬고 다시 시도합니다.
- **알림 초안**: 블록 알림의 임계값(켜진 칸 50%, 이탈 50%, 실패 30%)은 초안이며 실제 데이터에서 비율의 분포를 본 뒤 정합니다. 알림 채널은 아직 정하지 않았습니다. 저장소 Variables 에 `BREAKOUT_TELEGRAM` = `true` 를 두면 매트릭스와 같은 봇으로 `docs/breakout/data/alerts.txt`(새 돌파·새 이탈·오늘 실패·블록 알림, USD 기준)와 토요일 `brief.txt` 를 보냅니다.

## 아티팩트

`docs/breakout/breakout.artifact.html` 이 Claude 아티팩트 발행용 본문입니다(문서 골격 없이 `<title>` 부터). 매트릭스 아티팩트를 다시 발행하는 Claude 예약 작업(화~토 09:22·14:22 KST, 그날 빌드를 기다렸다가 발행)이 2026-10-23 부터 이 본문도 같은 차례에 돌파 지도 아티팩트로 발행합니다. 본문의 `Prices YYYY-MM-DD` 가 아티팩트의 것과 같으면 건너뛰고, 그날 돌파 지도 단계가 실패해 본문이 직전 빌드로 남아 있으면 그 사실만 보고합니다.

## 칸 아래 변동률과 종목 상세의 이동평균

칸 아래 세 줄은 그 종목의 변동률입니다. `D` 일간(전일 종가 대비), `W` 주간(5거래일 전 대비), `M` 30일(칸에 그린 기간, 30거래일 전 대비)이며 탭의 자(USD · QQQ 대비 · SPY 대비)를 따릅니다. 종목 상세 패널에는 같은 세 값이 "일간 · 주간 · 30일" 행으로 있고, 90일 차트에는 50일 단순이동평균(주황 굵은 실선), 89일 헐 이동평균(노랑 일점쇄선), 144일 헐 이동평균(파랑 일점쇄선)이 들어갑니다. 눈금은 가격과 Shelf 만으로 잡으므로 멀리 떨어진 이동평균은 잘려 나가고, 그때는 차트 가장자리에 현재가 대비 거리를 적습니다. 헐 이동평균은 `HMA(n) = WMA(2·WMA(n/2) − WMA(n), √n)` 이며 페이지에서 `cx + c` 로 계산합니다. 차트 위의 `USD · vs QQQ · vs SPY` 토글은 위의 1·2·3 탭과 같은 동작으로 지도 전체의 자를 바꿉니다.

## 화면의 명령

명령줄에 `NVDA` 같은 티커나 `SMH` 같은 ETF 를 치면 그 칸·블록으로 이동합니다. `1`/`2`/`3`(USD·QQQ·SPY), `11`~`14`(20일·55일·52주·사상 최고), `21`~`23`(정렬), `31`/`32`(블록·행 배치), `61`~`63`(구획), `71`/`72`(상장 필터), `98`(도움말), `SRM`·`MATRIX`(매트릭스로), `RRG`, `BRIEF`.
