# 미국 섹터·산업 ETF 모니터링 (섹터 전환 매트릭스 · 돌파 지도)

미국 섹터·산업 ETF 를 매일 아침 두 화면으로 보는 저장소입니다. 둘 다 같은 가격·구성종목 수집기 위에 서 있고, GitHub Actions 가 함께 만들어 GitHub Pages 로 냅니다.

| 화면 | 주소 | 코드 | 내용 |
|---|---|---|---|
| **섹터 전환 매트릭스** (SRM) | `https://tlzkrh1029.github.io/rrg/` | `terminal/` | 24 ETF 의 SPY 대비 상대수익률 백분위, 비율선, RRG 1단·2단, 신호, 주간 브리핑 |
| **돌파 지도** (BKM) | `https://tlzkrh1029.github.io/rrg/breakout/` | `breakout/` | 28 ETF 의 상위 10개 구성종목을 칸으로 놓고 20일·55일·52주·사상 최고 고점 돌파와 저점 이탈을 USD·QQQ 대비·SPY 대비로 표시 |

두 화면은 서로 탭으로 이어집니다(매트릭스의 `3) 돌파 지도`, 돌파 지도의 사이트 줄 `1) 매트릭스`). 매트릭스의 탭 순서는 1) 매트릭스 · 2) RRG 1단·2단 · 3) 돌파 지도 · 4) 주간 브리핑 · 5) 상장 로그 · 6) 도움말이고 명령줄의 `1`~`6` 도 같은 뜻입니다. 돌파 지도의 설계와 판정 규칙은 [breakout/README.md](breakout/README.md) 에 있습니다.

```
fetch_prices.py          ETF 가격·구성종목 수집기 (두 화면이 함께 씀)
fetch_etf_history.py     ETF 차트 자료와 전체 이력 사상 최고 캐시 (두 화면의 ETF 차트가 함께 씀)
terminal/                섹터 전환 매트릭스: listing_log.py · compute_terminal.py · fetch_contrib_extras.py → compute_contrib.py · fetch_news.py → compute_news.py → build_terminal.py, template.html
breakout/                돌파 지도: fetch_stock_prices.py → compute_breakout.py → build_breakout.py, match_listing.py, template.html
scripts/notify_telegram.py   텔레그램 전송 (--docs 로 두 화면 모두 보냄)
data/etf_meta.json       ETF 메타. screens 로 어느 화면에 들어가는지 표시 (terminal / breakout)
data/lists/              tradfi-ticker-lists 의 상장 목록 사본과 누적 상장 로그(log.json)
output/                  수집·계산 결과 (prices_wide.csv, holdings/, stocks/, etf_ath.json, etf-chart-data.json, terminal-data.json, breakout-data.json)
docs/                    GitHub Pages 본문. docs/index.html 이 매트릭스, docs/breakout/index.html 이 돌파 지도
.github/workflows/terminal.yml   매일 아침의 수집 → 계산 → 생성 → 커밋 → 알림
```

아래의 "가격 수집" 절은 수집기(`fetch_prices.py`)의 설명이고, "자동 갱신" 절이 매일의 흐름입니다. 스크립트는 [tradfi-ticker-lists](https://github.com/tlzkrh1029/tradfi-ticker-lists)의 `fetch_tradfi_tickers.py`와 같은 규칙을 따릅니다. 표준 라이브러리만 사용하고, 결과는 `output/`에 UTF-8 BOM CSV로 저장하며, 실행할 때마다 전체를 새로 받아 덮어씁니다.

## 가격 수집

### 결론

`fetch_prices.py`는 벤치마크 2개, 섹터 ETF 11개, 산업·테마 ETF 12개, 스타일·집중도 ETF 1개(MAGS), 그리고 돌파 지도에만 쓰는 ETF 5개(GDX, GRID, XOP, TAN, JETS)의 일봉을 API 키 없는 공개 출처에서 받아, SPY의 거래일을 기준 날짜 축으로 삼아 한 표에 정렬합니다. 같은 실행에서 각 ETF의 구성종목 상위 10개도 받아 날짜별 파일로 남깁니다.

```
python3 fetch_prices.py                  # 가격 + 구성종목, output/ 에 저장 (약 2분)
python3 fetch_prices.py --no-holdings    # 가격만
python3 fetch_prices.py --out <폴더>     # 저장 위치 변경
python3 fetch_prices.py --source yahoo   # 야후만 사용 (조정 종가를 한 출처로 통일할 때)
python3 fetch_prices.py --days 400       # 기준 축에 남길 거래일 수 변경 (기본 500)
```

종료 코드는 0(정상), 1(가격 수집에 실패한 티커가 하나라도 있음), 2(가격은 정상이지만 구성종목 일부 실패)입니다. 실행 중에는 티커마다 한 줄씩 출처와 받은 행 수를 출력하고, 마지막에 `warnings`를 표준 오류로 출력합니다. 2026-10-13 실행은 0으로 끝났고 경고는 없었습니다.

### 수집 대상

티커 목록은 스크립트 상단의 `BENCHMARKS`, `SECTORS`, `INDUSTRIES`, `STYLE`, `EXTRA`에 있으며, 산업·테마·스타일 티커를 무엇으로 나눌지는 `PARENT_SECTOR` 표에 있습니다. 티커를 추가하려면 이 목록만 고치면 됩니다. CSV의 열 순서와 행 순서는 이 목록 순서를 따르고, `STYLE`·`EXTRA`는 맨 뒤에 붙으므로 여기에 티커를 더해도 기존 열의 위치는 바뀌지 않습니다. `EXTRA`(GDX, GRID, XOP, TAN, JETS)는 돌파 지도의 블록으로만 쓰고 매트릭스의 보드·RRG 에는 넣지 않습니다(`data/etf_meta.json` 의 `screens` 가 어느 화면에 들어가는지 정합니다). `prices_summary.json`의 `groups`에 다섯 목록이 그대로 들어 있습니다. 2단 RRG의 대상은 `groups.industries`이고, `parent_sector`는 각 티커를 나누는 ETF만 알려 줍니다. `parent_sector`에는 MAGS도 들어 있으므로, 그 키 목록을 산업 목록으로 쓰면 안 됩니다.

| 구분 | 티커 | RRG 벤치마크(나누는 ETF) |
|---|---|---|
| 벤치마크 | SPY, QQQ | |
| 섹터 | XLK, XLF, XLV, XLY, XLC, XLI, XLP, XLE, XLU, XLB, XLRE | SPY (1단) |
| 산업·테마 | SMH, IGV, DRAM | XLK |
| | XBI, GNOM, ARKG | XLV |
| | KRE | XLF |
| | XHB | XLY |
| | XME | XLB |
| | ITA, PAVE | XLI |
| | URA | XLE |
| 스타일·집중도 | MAGS | SPY. 1단 보드에 섹터와 같은 축으로 그리되, 섹터 11개를 대상으로 하는 집계(사분면 개수, 섹터 간 순위)에는 넣지 않음 |
| 돌파 지도 전용 | GDX, GRID, XOP, TAN, JETS | RRG 에는 넣지 않음. 가격은 돌파 지도 ETF 패널의 차트에, 구성종목은 블록에 씀 |

### 출력 파일

`output/`에 저장하며, Excel에서 한글이 깨지지 않도록 UTF-8 BOM을 붙입니다. 가격은 소수점 넷째 자리까지, 거래량은 정수로 적습니다.

| 파일 | 형식 | 열 |
|---|---|---|
| `prices.csv` | long. 날짜 오름차순, 같은 날짜 안에서는 수집 대상 순서 | `date`, `ticker`, `open`, `high`, `low`, `close`, `adj_close`, `volume` |
| `prices_wide.csv` | wide. 행은 SPY 기준 축의 날짜, 열은 티커별 `adj_close`. 이 파일 하나로 비율 계산이 됩니다 | `date`, `SPY`, `QQQ`, `XLK`, …, `DRAM`, `MAGS`, `GDX`, …, `JETS` |
| `prices_summary.json` | 실행 결과 요약 | `generated_at`(UTC), `source_order`, `tickers`(티커별 `{source, adj_close_available, first_date, last_date, rows, missing_days, gap_days, off_axis_dates, rows_before_axis, partial_bar_dropped, attempts}`), `base_axis {ticker, first_date, last_date, days}`, `failed_tickers`, `parent_sector`, `groups`, `holdings`, `holdings_failed`, `checks`, `warnings` |
| `holdings/YYYY-MM-DD.csv` | 구성종목 상위 10개 스냅샷. 파일명은 실행일이며 같은 날짜 파일은 덮어씁니다 | `etf`, `rank`, `symbol`, `name`, `weight_pct` |
| `stocks/prices_wide.csv`, `stocks/ath.json`, `stocks/summary.json` | 돌파 지도의 구성종목 일봉(같은 SPY 축)과 사상 최고 캐시. `breakout/fetch_stock_prices.py` 가 씁니다 | [breakout/README.md](breakout/README.md) 참고 |
| `news/earnings.json`, `news/calendar.json`, `news/filings.json`, `news/filing_text.json`, `news-data.json` | 뉴스·실적·공시. `terminal/fetch_news.py` 가 SEC 8-K 실적, 최근 90일 공시 목록과 8-K 첫 문장(캐시), Finnhub 실적 일정을 받고(`news/headlines.json` 은 같은 실행 안에서만 쓰고 저장소에 두지 않음), `terminal/compute_news.py` 가 페이지용으로 계산합니다 | 아래 "뉴스·실적" 절 참고 |
| `etf_ath.json`, `etf-chart-data.json` | ETF 차트의 전체 이력 사상 최고 캐시와 90일 차트 자료. `fetch_etf_history.py` 가 씁니다 | 아래 "ETF 차트" 절 참고 |

`prices_wide.csv`의 빈 칸은 그 날짜에 그 티커의 데이터가 없다는 뜻입니다. 직전 값으로 채우지 않으므로, 비율을 계산할 때 빈 칸은 건너뛰어야 합니다.

### 날짜 축 규칙

모든 티커의 종가가 같은 날짜 축에 놓여야 산업·테마 ETF ÷ 상위 섹터 ETF를 날짜별로 나눌 수 있습니다. 그래서 다음 규칙으로 정렬합니다.

- SPY의 거래일 중 최근 500일(`--days`)이 기준 축입니다. `base_axis`에 축의 첫 날짜, 마지막 날짜, 길이를 적습니다.
- 어떤 티커에 축의 날짜 데이터가 없으면 그 칸은 빈 값으로 둡니다. 빈 칸 수는 티커별 `missing_days`에 적고, 상장 이후 구간에서만 센 빈 칸 수는 `gap_days`에 따로 적습니다. 2026-04-02에 상장한 DRAM은 축 500일 중 367일이 상장 전이라 `missing_days`가 367이지만 `gap_days`는 0입니다.
- 축에 없는 날짜에만 데이터가 있는 티커가 있으면 그 날짜는 버리고 `off_axis_dates`와 `warnings`에 적습니다.
- 축보다 앞선 날짜의 데이터는 조용히 버리고 `rows_before_axis`에 개수만 적습니다.
- 뉴욕 시간 16:15 이전에 실행하면 그날의 봉은 아직 장중 값이므로 버리고 `partial_bar_dropped`에 날짜를 적습니다.

스크립트는 마지막에 다음을 스스로 확인해서 `checks`에 적고, 어긋나면 `warnings`에 문장으로 남깁니다.

| 항목 | `checks` 키 |
|---|---|
| `prices_wide.csv`의 행 수가 `base_axis.days`와 같은가 | `wide_rows_equal_base_axis_days` |
| SPY, QQQ, 섹터 11개, MAGS의 `missing_days`가 5 이하인가 | `core_missing_days_over_limit` (넘은 티커와 개수) |
| 모든 티커의 `last_date`가 SPY의 `last_date`와 같은가 | `last_date_differs_from_base` (다른 티커와 날짜) |
| 조정 종가가 없는 출처로 받은 티커가 있는가 | `tickers_without_adj_close` |

### 출처와 한계

가격은 티커마다 1순위 Stooq 일봉 CSV(`https://stooq.com/q/d/l/?s=spy.us&i=d`), 2순위 야후 파이낸스 chart 엔드포인트(`https://query1.finance.yahoo.com/v8/finance/chart/SPY?range=2y&interval=1d`, `range`는 `--days`에 따라 2y·5y·10y·max 중 하나) 순서로 시도하고, 실제로 쓴 출처를 `source`에, 실패한 시도의 원인을 `attempts`에 적습니다.

- **Stooq에는 조정 종가 열이 따로 없습니다.** Stooq에서 받은 티커는 `close`를 `adj_close`에 복사하고 `adj_close_available`을 `false`로 둡니다. 야후에서 받은 티커의 `adj_close`는 배당·분할 조정 종가입니다. 한 실행 안에서 두 종류가 섞이면 비율선이 배당만큼 어긋나므로 `warnings`에 기록합니다. 이때는 `--source yahoo`로 다시 실행하면 전 티커가 같은 기준이 됩니다.
- Stooq는 일일 요청 한도를 넘으면 CSV 대신 안내 문구를 돌려주며, 연결이 아예 되지 않는 환경도 있습니다. 연결 수준의 실패가 3번 이어지면 남은 티커에서는 Stooq를 건너뛰고(`attempts`에 `skipped`로 표시) 바로 야후로 갑니다. 저장소의 2026-10-13 결과물을 만든 환경에서는 Stooq에 연결할 수 없어서(TLS 연결이 끊기거나 CSV 대신 HTML 페이지가 옴) 26개 티커 모두 야후에서 받았습니다.
- 야후 chart 엔드포인트는 공식 지원 대상이 아니며, User-Agent가 없으면 거부합니다. HTTP 429를 받으면 기다렸다가 다시 시도하고, `query1`이 실패하면 `query2`로 넘어갑니다. 조정 종가는 배당이 지급될 때마다 과거 값이 전부 다시 계산되므로, 일부만 이어 붙이지 말고 매번 전체를 다시 받아야 합니다. 이 스크립트는 그렇게 동작합니다.
- 기본 축 길이 500 거래일을 받기 위해 야후에서는 5년치를 요청하고 축 밖의 과거 데이터는 버립니다. 단일 종목이 아닌 ETF만 다루므로 상장폐지·티커 변경은 고려하지 않습니다.
- DRAM은 2026년 4월 상장이라 2026-10-13 기준 133개 봉만 있습니다. 상장 전 구간은 빈 칸으로 두며 오류나 경고로 다루지 않습니다.
- MAGS(Roundhill Magnificent Seven ETF)는 AAPL, MSFT, NVDA, AMZN, TSLA, META, GOOGL 일곱 종목을 동일가중(각 약 14%)으로 담고 분기마다 리밸런스합니다. 노출의 대부분을 총수익스왑으로 얻고 담보로 단기 국채를 보유하므로, 구성종목 상위 10개에는 국채·현금성 ETF(XBOX)·종목별 직접 보유분(5~6%)이 나오고 MSFT는 10위 밖에 있습니다. 이 표로는 종목별 실제 노출을 읽을 수 없습니다. 일곱 종목이 IT(XLK), 경기소비재(XLY), 커뮤니케이션(XLC) 세 섹터에 걸쳐 있어서 상위 섹터 하나를 고를 수 없으므로 SPY로 나눕니다. MAGS÷SPY는 "동일가중 Mag 7이 시장을 이기는가"를 뜻하며, 시가총액가중인 SPY가 일곱 종목을 약 35% 담고 있어서 진폭이 다소 줄어듭니다. 동일가중이라 한 종목의 기여가 7분의 1로 제한되므로(NVDA 비중: MAGS 약 14%, SPY 8.2%, XLK 15.3%), NVDA 한 종목이 끌고 나머지 여섯이 뒤처지는 장에서는 MAGS÷SPY의 상승 폭이 시가총액가중 Mag 7 바스켓보다 작게 나오거나 내려갈 수도 있습니다. 그래서 XLK÷SPY, QQQ÷SPY와 같이 읽어야 합니다. MAGS는 2023-04-11 상장이라 기본 500일 축에는 빈 칸이 없지만, `--days`를 약 880 이상으로 늘리면 상장 전 구간이 생겨 `core_missing_days_over_limit`에 걸립니다. 스왑 금융비용과 보수 때문에 이론적인 동일가중 바스켓보다 연 1.5% 안팎 뒤처지는데, 주간 RRG에는 영향이 없지만 긴 비율선에는 보입니다.
- 구성종목은 stockanalysis.com의 ETF holdings 페이지(`https://stockanalysis.com/etf/smh/holdings/`)에서 첫 번째 표를 읽습니다. 이 사이트는 브라우저처럼 보이는 Accept 헤더가 없으면 403을 돌려주고, 자동 수집을 막을 수 있습니다. 한 ETF가 막히면 그 ETF만 건너뛰고 `holdings_failed`에 적으며 종료 코드는 2가 됩니다. 전부 막히면 운용사 공식 페이지의 CSV로 출처를 바꾸는 편이 낫습니다.
- 구성종목의 `symbol`은 페이지에 적힌 그대로입니다. 해외 상장 종목은 `TSX: NXE`, `KRX: 005930`처럼 거래소가 붙습니다. 비중은 페이지 갱신 시점의 값이며 당일 종가 기준이 아닐 수 있습니다.
- **Roundhill 펀드(MAGS, DRAM)는 운용사의 일일 보유 파일**(`https://www.roundhillinvestments.com/assets/data/FilepointRoundhill.40RU.RU_Holdings_MMDDYYYY.csv`, 뉴욕 날짜 기준 오늘부터 15일을 거슬러 처음 있는 파일)에서 받습니다. 이 펀드들은 종목마다 총수익스왑 여러 건과 직접 보유분을 함께 들고 있으므로, 같은 CUSIP/SEDOL의 줄을 합산해 종목별 순노출을 만듭니다. 이 값이 운용사 페이지의 Top Holdings와 같습니다(MAGS는 7종목 각 13~15%). 담보로 보유한 국채·MMF·XBOX와 스왑을 상쇄하는 음수 Cash & Other 줄은 뺍니다. `symbol`은 stockanalysis 표기와 맞추어 `KRX: 005930`, `SSE: 688825`처럼 적습니다. 파일을 받지 못하면 stockanalysis.com 표로 대체하고 `warnings`에 남기며, `prices_summary.json`의 `holdings.sources`에 ETF별 출처를, `holdings.roundhill_file`에 파일 날짜를 적습니다.
- **야후 chart 응답에 마지막 거래일의 봉이 아직 없는 티커**(저녁에도 일부 ETF가 전날까지만 옴)는 같은 응답의 `meta.regularMarketPrice`·`regularMarketTime`이 그날 정규장 종료 이후 값이면 그 값으로 그날 봉을 만들어 넣고 `last_bar_from_quote`에 날짜를 적습니다. 그래도 비어 있으면 `compute_terminal.py`가 최근 3거래일 안의 마지막 종가로 계산하고 `stale`에 날짜를 적어 화면에 † 표시로 보여 줍니다(비워 두면 그 ETF가 백분위 풀에서 빠져 다른 ETF의 순위까지 흔들립니다).
- 결과 CSV는 2026-10-13 실행 스냅샷(기준 축 2024-10-14 ~ 2026-10-12)입니다. 매주 보드를 갱신하려면 스크립트를 다시 실행해야 합니다. `holdings/`에는 실행일별 파일이 쌓입니다.

## 자동 갱신 (GitHub Actions → GitHub Pages → 텔레그램)

`.github/workflows/terminal.yml` 이 사람 손 없이 두 화면을 갱신합니다. 흐름은 수집 → 계산 → 페이지 생성 → 커밋 → 알림이며, 모든 단계가 표준 라이브러리만 씁니다.

| 시각 (KST) | 하는 일 |
|---|---|
| 화~금 08:10 | `tradfi-ticker-lists` 를 받아 상장 목록을 갱신하고(받지 못하면 `data/lists/` 의 사본을 씀), `terminal/listing_log.py` 로 직전 목록과 비교한 신규·이탈을 `data/lists/log.json` 에 쌓고(실패해도 계속), `fetch_prices.py` 로 ETF 가격·구성종목을 받고, `fetch_etf_history.py` 로 두 화면의 ETF 차트 자료를 만들고(전체 이력은 월 1회 받음, 실패해도 계속), `breakout/fetch_stock_prices.py` 로 구성종목 약 240개의 일봉을 받아 `breakout/compute_breakout.py` 로 계산한 뒤(실패해도 다음 단계는 계속), `terminal/fetch_contrib_extras.py` 로 기여도용 보조 가격과 환율을 받고(실패해도 계속), `terminal/fetch_news.py` 로 SEC 실적과 Finnhub 헤드라인·실적 일정을 받은 뒤(약 4분, 실패해도 계속), `terminal/compute_terminal.py` → `terminal/compute_contrib.py` → `terminal/compute_news.py` → `terminal/build_terminal.py` 로 `docs/` 를 다시 만듭니다(종목 상세에 돌파 지도의 계산을, Holdings 패널에 ETF 차트·기여도·실적을, 뉴스 칼럼에 헤드라인과 요약을, 주간 브리핑에 주도 종목을 끼움). 이어서 `breakout/compute_breakout.py` → `breakout/build_breakout.py` 로 `docs/breakout/` 을 만듭니다(이 세 단계가 실패해도 매트릭스 쪽 빌드와 커밋은 그대로 진행되고, 돌파 지도는 직전 빌드로 남습니다). 모두 커밋한 뒤 직전 빌드와 비교해 변화가 있으면 텔레그램으로 매트릭스 조건 알림을 보냅니다. |
| 토 07:05 | 금요일 종가로 위와 같은 일을 한 뒤, 주간 브리핑(`docs/data/brief.txt`)을 텔레그램으로 보냅니다. |

돌파 지도의 알림 채널은 아직 정하지 않았습니다. 빌드는 매일 `docs/breakout/data/alerts.txt`(새 돌파·새 이탈·실패·블록 알림)와 `brief.txt`(주간 브리핑)를 써 두기만 하고, 저장소 Variables 에 `BREAKOUT_TELEGRAM` = `true` 를 추가하면 같은 봇으로 보내기 시작합니다.

Actions 탭의 **Run workflow** 로 언제든 수동 실행할 수 있고, `digest` 를 켜면 브리핑도 함께 보냅니다.

GitHub 의 예약 실행은 보통 예약 시각보다 2~4시간 늦게 시작됩니다(빌드 커밋이 평일에는 10:18~12:32 KST, 토요일에는 09:21~10:42 KST 사이였습니다). Claude 아티팩트 두 개(📊 Weekly RRG 와 돌파 지도)는 Claude 예약 작업이 화~토 09:22 KST 와 14:22 KST 에 main 의 `docs/terminal.artifact.html` 과 `docs/breakout/breakout.artifact.html` 을 읽어 다시 발행하므로 Pages 와 같은 빌드를 보여 줍니다. 이 작업은 그날 만들어진 빌드(`docs/data/terminal-data.json` 의 `generated_at` 이 당일)가 올라올 때까지 10분 간격으로 기다렸다가 발행하고, 첫 실행에서 발행했으면 두 번째 실행은 확인만 하고 끝냅니다. 그날 빌드가 끝내 오지 않으면 두 번째 실행이 main 의 마지막 빌드로 발행하고 그 사실을 보고합니다. 보고는 토요일에는 `brief.txt`, 평일에는 `alerts.txt` 의 내용입니다.

### 한 번만 해 두는 설정

1. **GitHub Pages**: 저장소 Settings → Pages → Build and deployment 에서 Source 를 `Deploy from a branch`, Branch 를 `main` / `/docs` 로 두고 저장합니다. 이후 `https://tlzkrh1029.github.io/rrg/` 가 최신 보드입니다.
2. **상장 목록(선택)**: `tradfi-ticker-lists` 는 비공개 저장소라 Actions 가 그냥은 받지 못합니다. 이 저장소의 `data/lists/` 에 사본(2026-09-27)을 두었으므로 그대로 두어도 동작하지만, 상장 점을 매주 새로 받으려면 둘 중 하나를 합니다. (a) `tradfi-ticker-lists` 를 공개로 전환, (b) GitHub Settings → Developer settings → Personal access tokens → Fine-grained 에서 `tradfi-ticker-lists` 의 Contents 읽기 권한만 가진 토큰을 만들어 이 저장소 Secrets 에 `LISTS_TOKEN` 으로 추가. 새 목록을 받으면 `data/lists/` 도 함께 갱신·커밋됩니다. 5) 상장 로그 탭의 기록도 새 목록을 받은 날에만 쌓입니다.
3. **뉴스·실적**: 저장소 Settings → Secrets and variables → Actions 에 `SEC_USER_AGENT`(SEC EDGAR 가 요구하는 이름과 연락처, 예: `이름 주소@도메인`)와 `FINNHUB_TOKEN`(finnhub.io 무료 등급 API 키)을 둡니다. 없으면 그 출처만 건너뛰고 나머지는 정상 동작합니다. 두 값은 로그와 파일 어디에도 남기지 않습니다.
4. **텔레그램**: @BotFather 에게 `/newbot` 을 보내 봇을 만들고 토큰을 받습니다. 그 봇에게 아무 메시지나 보낸 뒤 `https://api.telegram.org/bot<토큰>/getUpdates` 를 열어 `"chat":{"id":…}` 의 숫자를 확인합니다. 저장소 Settings → Secrets and variables → Actions 에 `TELEGRAM_BOT_TOKEN` 과 `TELEGRAM_CHAT_ID` 를 추가합니다. 두 값이 없으면 알림 단계는 건너뛰고 나머지는 정상 동작합니다.

### 파일

| 파일 | 역할 |
|---|---|
| `fetch_etf_history.py` | 두 화면의 ETF 차트 자료를 만듭니다. ETF 마다 Yahoo 전체 이력(range=max)을 월 1회 받아 90일 창 이전의 최고 조정 종가와 SPY·QQQ·상위 섹터 대비 최고 비율을 `output/etf_ath.json` 에 캐시하고, 매일 `output/prices_wide.csv` 에서 90일 종가·이동평균용 창 앞 종가·52주 고점·사상 최고를 계산해 `output/etf-chart-data.json` 을 씁니다. 받지 못한 ETF 는 이전 캐시나 2년 가격 파일로 대신하고 종료 코드 2 를 냅니다. |
| `terminal/compute_terminal.py` | `output/prices_wide.csv` 로 백분위·비율선·RRG(1단 섹터 ÷ SPY, 2단 산업·테마 ÷ 상위 섹터, 스타일 MAGS ÷ SPY)·신호를 계산해 `output/terminal-data.json` 을 씁니다. 규칙은 파일 머리의 설명에 있습니다. |
| `terminal/fetch_contrib_extras.py` | 기여도용 보조 자료를 `output/stocks/` 에 받습니다. 최근 45일 스냅샷에서 터미널 ETF 와 SPY·QQQ 의 상위 10에 들었지만 돌파 지도 수집에 없는 종목의 일봉(`extra_prices_wide.csv`), 해외 종목 통화별 1달러당 환율(`fx_wide.csv`, Yahoo `KRW=X` 등), 종목별 통화와 실패 목록(`extra_summary.json`). 실패해도 다음 단계는 그대로 갑니다. |
| `terminal/compute_contrib.py` | 구성종목 기여도를 계산해 `output/contrib-data.json` 을 씁니다. 규칙은 아래 "기여도" 절과 파일 머리의 설명에 있습니다. 실패하면 그날 페이지는 기여도 블록과 주도 종목 없이 만들어집니다. |
| `terminal/fetch_news.py` | 터미널 ETF 상위 10 종목의 뉴스·실적을 `output/news/` 에 받습니다. SEC EDGAR 의 8-K 가운데 Item 2.02(실적) 접수 시각(`earnings.json`), 같은 제출 목록의 최근 90일 공시와 내부자 신고 건수(`filings.json`), 8-K 본문의 첫 문장(`filing_text.json`, 접수 번호마다 한 번만 받음), Finnhub 의 이번 주를 포함한 최근 4주 헤드라인(`headlines.json`, 저장소에 두지 않음)과 35일 안 실적 일정(`calendar.json`), NASDAQ Trader 상장 목록으로 정한 TradingView 심볼. SEC 는 초당 10건, Finnhub 는 분당 60건 한도보다 느리게 보냅니다. 받지 못한 부분은 이전 파일을 그대로 둡니다. |
| `terminal/compute_news.py` | 뉴스·실적을 페이지용으로 계산해 `output/news-data.json` 을 씁니다. 실적 발표마다 장 시작 전·장중·장 마감 후를 가르고 발표 직전 종가에서 발표 뒤 첫 종가까지의 반응을 적으며, ETF 마다 기여도와 실적 일정으로 '왜 움직였나' 요약 문장을 만듭니다. |
| `terminal/listing_log.py` | 상장 목록을 직전 상태와 비교해 신규·이탈을 `data/lists/log.json` 에 쌓습니다. 규칙은 아래 "상장 로그" 절에 있습니다. |
| `terminal/build_terminal.py` | 계산값, 최신 두 구성종목 스냅샷(변동 %p), 상장 목록, `data/etf_meta.json` 을 `terminal/template.html` 에 끼워 `docs/index.html`(Pages 용)과 `docs/terminal.artifact.html`(Claude 아티팩트 발행용)을 만듭니다. 두 본문은 `5) 돌파 지도` 탭의 주소만 다릅니다(Pages 는 `breakout/`, 아티팩트는 돌파 지도 아티팩트를 새 탭으로). 직전 빌드(`docs/data/terminal-data.json`)와 비교해 `docs/data/alerts.json`·`alerts.txt` 와 주간 브리핑 `docs/data/brief.txt` 를 씁니다. 구성종목 표의 종목 행을 누르면 펼쳐지는 종목 상세(일간·주간·30일 변동률, 90일 가격선과 50 SMA·89 HMA·144 HMA, 네 지평의 돌파 상태, 그 종목을 담은 다른 ETF)는 `output/breakout-data.json`(없으면 `docs/breakout/data/` 의 직전 사본)에서 이 보드의 구성종목만 추려 끼워 넣습니다. |
| `terminal/template.html` | 터미널 화면 템플릿. `__HOLD__`, `__DATA__`, `__PRICE_ASOF__` 같은 토큰을 빌드가 채웁니다. 화면을 고칠 때는 이 파일을 고칩니다. 주소에 해시가 없으면 보드 위에 대문(cover)이 먼저 덮이고 LAUNCH TERMINAL 로 엽니다. `#board`·`#rrg`·`#brief`·`#help` 로 들어오면 대문을 건너뛰며(텔레그램 링크는 `#board`), 대문의 Skip 을 켜면 그 브라우저에서는 다시 보이지 않습니다. 보드에서 `0` 또는 `COVER` 를 입력하면 다시 엽니다. |
| `breakout/` | 돌파 지도. 수집기 `fetch_stock_prices.py`, 상장 대조 `match_listing.py`, 계산 `compute_breakout.py`, 페이지 생성 `build_breakout.py`(`docs/breakout/index.html` 과 `docs/breakout/breakout.artifact.html`), 템플릿 `template.html`. 자세한 설명은 [breakout/README.md](breakout/README.md). |
| `scripts/notify_telegram.py` | `brief.txt`(`--mode digest`) 또는 `alerts.txt`(`--mode alerts`, 비어 있으면 보내지 않음)를 텔레그램 봇 API 로 보냅니다. `--docs docs/breakout` 으로 돌파 지도의 본문을 보냅니다. |
| `data/etf_meta.json` | ETF 이름·순자산·보유 종목 수·출처 URL(2026-09-26 stockanalysis 기준, MAGS 는 2026-10-13, 돌파 지도 전용 5개는 2026-08~09), 한글 이름 `ko`, 분류 `tag`·`cat`, 들어가는 화면 `screens`. 순자산은 자동 갱신되지 않습니다. |
| `output/holdings/2026-09-26.csv` | 변동 %p 의 첫 비교 기준이 되는 스냅샷(시안 v2 의 구성종목 데이터에서 복원). |
| `data/lists/log.json` | 5) 상장 로그 탭의 누적 기록. 처음 내용은 이 저장소에 커밋된 목록 사본 여섯 개(2026-09-27 ~ 10-27)를 차례로 반영해 만들었고, 그 뒤로는 매일의 빌드가 덧붙입니다. |

### 알림 조건

직전 빌드와 비교해 다음이 생기면 `alerts.txt` 에 적고 텔레그램으로 보냅니다. RRG 국면 변화(벤치마크 대비, 산업·테마는 SPY 대비도), 새로 켜진 신호(52주 상대 신고가, 비율선 50·200일선 돌파·이탈, 백분위 ±30 급변, 1주 반등 미확인), 전환순 상위 3 진입. 같은 종가로 다시 빌드하면 변화로 치지 않습니다. 본문은 ETF 단위로 묶어 1W 백분위가 높은 순으로 적고, 머리글마다 `[1W/1M/3M/6M/12M 백분위]`와 벤치마크 대비 RRG 국면을 붙입니다. 페이지의 Changes 패널도 같은 순서·같은 수치를 보여 줍니다.

### ETF 차트 (Holdings 패널 · 돌파 지도 ETF 패널)

Holdings 패널 맨 위에 그 ETF 자체의 90일 차트가 늘 열려 있습니다. 돌파 지도에서 ETF 이름을 눌러 여는 ETF 패널에도 같은 차트가 있습니다.

- **내용**: 일간·주간·30일 변동률, 90일 종가선과 50 SMA·89 HMA·144 HMA, 20일 Shelf(돌파 지도에서는 고른 지평)와 52주 고점 선, 네 지평(20일·55일·52주·사상 최고)의 돌파 상태. 판정 규칙은 돌파 지도의 종목 칸과 같습니다.
- **자**: 매트릭스에서는 USD · vs QQQ · vs SPY 에 산업·테마 ETF 는 vs 상위 섹터까지 고릅니다. 이 토글은 아래 기여도 블록의 기준과 같은 값이라 둘이 함께 바뀝니다. 돌파 지도에서는 지도 전체의 자(USD · QQQ 대비 · SPY 대비)를 따릅니다.
- **사상 최고**: USD·비율 모두 전체 이력에서 잡습니다. `fetch_etf_history.py` 가 월 1회 받아 둔 "90일 창 이전 최고"(`output/etf_ath.json`)와 2년 가격 파일 안의 창 이전 최고, 52주 고점 가운데 큰 값입니다. 캐시를 받은 뒤 생긴 배당·분할 재조정은 경계 직전 거래일의 값 비로 옮겨 반영합니다. 지평 표의 '사상 최고' 옆에 이력의 첫 해를 적고, 캐시를 쓰지 못한 날은 2년 파일의 첫 해가 적힙니다. 돌파 지도 종목 칸의 비율 사상 최고는 지금처럼 2년 창 안에서 잡습니다.
- 차트가 생기면서 Holdings 패널과 돌파 지도 ETF 패널의 12주 비율선 두 줄(vs SPY 12W · vs QQQ 12W)은 뺐습니다. 매트릭스 표의 12주 비율선 열은 그대로입니다.

### 뉴스·실적 (뉴스 칼럼 · Holdings 패널)

- **자리**: 화면 폭이 1,860px 이상이고 1) 매트릭스 탭이면 매트릭스 · Holdings · News 세 칼럼이 됩니다. News 칼럼은 ETF 를 고르면 'ETF 모드'(왜 움직였나 요약, 상위 10 종목의 실적 일정, 최근 7일 헤드라인)이고, 구성종목 행을 눌러 종목 상세를 열면 '종목 모드'(최근 실적과 다음 실적일, TradingView 링크 세 개, 그 종목의 최근 4주 주별 기사와 공시)로 바뀝니다. '← 전체'를 누르면 ETF 모드로 돌아갑니다. 그보다 좁으면 같은 내용이 Holdings 패널 안에 들어갑니다(ETF 차트 아래에 요약과 접힌 헤드라인 줄, 종목 상세 안에 그 종목의 뉴스).
- **실적**: SEC EDGAR 8-K 가운데 Item 2.02(Results of Operations and Financial Condition)가 든 것을 실적 발표로 봅니다. 접수 시각(뉴욕)이 09:30 전이면 장 시작 전, 16:00 이후면 장 마감 후이며, 반응은 발표 직전 종가에서 발표 뒤 첫 종가까지의 변화입니다. 구성종목 표의 티커 아래에 최근 발표일(기여도 기간 안이면 호박색) 또는 14일 안의 예정일을, 종목 상세의 차트에 발표일 표시선을, 기여도 행과 주도 종목 표에 기간 안의 발표를 적습니다. 6-K 를 내는 외국 발행사(TSM·ASML 등)와 미국 밖 상장 종목은 8-K 실적이 없습니다.
- **종목 모드의 최근 4주**: 이번 주부터 4주(뉴욕 월요일 시작)를 주마다 한 덩어리로 보여 줍니다. 덩어리 머리에는 그 주의 기사 수와 종가 변화, 그 주에 접수한 SEC 공시 칩(8-K 는 항목 번호와 한국어 이름, 10-Q·10-K 는 보고서 종류)이 있고, 그 아래에 기사 묶음 두 줄이 있습니다. 회사 이름·티커·흔한 낱말을 뺀 제목 낱말이 셋 이상, 또는 둘 이상이면서 짧은 쪽의 30% 이상 겹치고 4일 안에 나온 기사는 한 묶음으로 보고 '비슷한 기사 +n'으로 적습니다. 순서는 제목에 회사 이름이나 티커가 든 묶음이 먼저이고, 그 안에서는 묶인 기사 수, 제목의 실적·배당·목표가 같은 낱말, 출처(보도자료 · 주요 매체) 순입니다. 주마다 네 묶음까지 남기고 두 줄만 보이며, 나머지는 '나머지 n건'을 누르면 나옵니다. 4주보다 앞선 90일 안의 공시와 내부자 신고(Form 4·144·3·5) 건수는 맨 아래 '그 전' 줄에 있습니다.
- **8-K 이름**: 8-K 는 본문을 한 번 열어 첫 항목의 첫 문장을 가져옵니다(실적 2.02 만 든 것은 열지 않음). 그 문장에 dividend 가 있으면 '배당', repurchase 가 있으면 '자사주 매입'처럼 이름을 바꾸고, 없으면 항목 이름(8.01 기타 중요 사항 등)을 씁니다. 칩에 커서를 올리면 그 문장이 보입니다. 하루에 새로 여는 본문은 250건까지이고 나머지는 다음 실행에서 엽니다.
- **Finnhub 4주**: 종목마다 한 번에 4주를 받고, 응답이 50건 이상인데 가장 오래된 기사가 창 시작보다 이틀 넘게 늦으면(응답 건수 상한에 걸린 것으로 보고) 그 앞 구간을 한 번 더 받습니다. 그래도 비면 그 주 머리에 '기사 n건 이상'으로 적습니다. ETF 모드의 헤드라인 목록은 지금처럼 상위 10 종목의 최근 7일 기사입니다.
- **요약**: '왜 움직였나'는 기여도(1개월, 산업·테마는 상위 섹터 대비, 나머지는 SPY 대비, 지금 구성)와 실적 일정으로 만든 문장이며 Pages 와 아티팩트에 똑같이 들어갑니다. 아티팩트에는 Claude 예약 작업이 웹 검색으로 찾은 이유 한두 문장을 더할 수 있습니다(페이지의 `<script id="claudeNotes">` 자리에 `{"asof": 종가 기준일, "at": 작성 시각, "etfs": {ETF: {"text": [문장], "src": [{"t": 제목, "u": 주소}]}}}` 를 넣으면 종가 기준일이 같을 때만 보입니다).
- **TradingView**: 종목 모드의 Key facts · Top news · Latest 링크는 TradingView 종목 뉴스 페이지의 세 구역으로 바로 갑니다. TradingView 뉴스 위젯은 2026-10-28 에 Pages 에서 확인해 보니 2024년 기사 같은 오래된 기사를 띄우고 기사 열기도 일정하지 않아서 꺼 두었습니다. 다시 켜려면 `terminal/build_terminal.py` 의 `TV_WIDGET_PAGES` 를 `True` 로 바꿉니다(켜도 Pages 에서만 나오고, 아티팩트는 외부 스크립트를 쓸 수 없어 링크만 둡니다).

### 기여도 (Holdings 패널 · 주간 브리핑)

Holdings 패널의 구성종목 표 아래에 "이 ETF 를 어느 종목이 움직였나"를 보여 주는 기여도 블록이 있습니다.

- **계산**: 기여 = 기간 시작 비중 × 기간 수익률(USD). 기간은 1D·1W·1M(1·5·21 거래일)입니다. 기간 시작 비중은 스냅샷 비중을 가격 변화로 기간 시작 시점까지 옮긴 값입니다. 스냅샷이 가리키는 종가는 ETF 마다 다르므로(SPDR 은 하루 전, iShares·VanEck 등은 이틀 전, Roundhill 은 당일) 연속한 스냅샷의 비중 변화와 가격 변화가 가장 잘 맞는 날을 매번 골라 씁니다.
- **기준**: USD, vs QQQ, vs SPY, 산업·테마 ETF 는 vs 상위 섹터까지 고릅니다. 고르는 토글은 위의 ETF 차트 블록에 있습니다. 상대 기준에서는 ETF 쪽과 벤치마크 쪽의 기여 목록을 나란히 놓고, 두 수익률의 차이를 머리에 적습니다. 기본은 산업·테마가 상위 섹터, 나머지가 SPY 입니다.
- **나머지와 미설명분**: 나머지 = ETF 수익률 − 표에 있는 종목 기여의 합입니다. 표의 비중 합이 98% 이상이면(MAGS·DRAM) 다른 종목이 남지 않으므로 '미설명분'으로 적고 원인(현금·보수·시차·스왑)을 붙입니다.
- **경고**: 상위 10 비중이 50% 미만이면 나머지가 결과를 좌우했다고 적습니다. 균등가중 ETF(XHB·KRE·XBI·XME)는 지금 상위 10에 기간 중 오른 종목이 많이 들어오므로, 기간 시작에 가장 가까운 스냅샷의 상위 10으로 다시 계산한 '기간 시작 구성'으로 바꿔 볼 수 있습니다.
- **해외 종목**: 현지 종가를 같은 날 환율로 나눠 USD 로 바꾸고, 행 아래에 현지 수익률과 환율 수익률을 적습니다. 해외 종목은 미국 장보다 먼저 마감하므로 1D 에서는 행에 '시차'를 붙입니다.
- **지표와 히트맵**: 상승 종목 수, 상위 1개·3개 집중도(합계와 같은 방향 기여의 합 가운데 몫), 나머지 평균, 최대 상승일·하락일, 최근 8주 주간 기여 히트맵을 함께 둡니다.
- **주도 종목**: 주간 브리핑 탭 오른쪽에 터미널 ETF 의 상위 10 종목 기여를 종목별로 모은 순위(USD·SPY 대비, 1W·1M)를 둡니다. 순위는 한 ETF 에 준 가장 큰 기여로 매기고, 같은 회사의 여러 주식(GOOGL·GOOG)은 합칩니다. 텔레그램 주간 브리핑에도 '주도 종목' 두 줄(1W, USD)이 들어갑니다.

### 상장 로그 (5) 탭)

세 거래소 목록(바이낸스 bStocks 현물, 바이낸스 TradFi 선물, 하이퍼리퀴드 HIP-3)을 받을 때마다 직전 상태와 비교해 신규와 이탈을 날짜순으로 쌓습니다.

- **날짜**: 바이낸스 TradFi 선물은 계약마다 거래소가 밝힌 상장일(`onboard_date`)을 그대로 씁니다. bStocks 와 하이퍼리퀴드 목록에는 날짜가 없어서, 목록에서 처음 보인 수집의 날짜(KST)를 쓰고 직전 수집 시각과 이번 수집 시각을 함께 남깁니다. 페이지는 두 수집이 4일 넘게 떨어져 있으면 '9/27 이후'처럼 시작일을 함께 적습니다. 이탈은 목록에서 사라진(하이퍼리퀴드는 상장폐지로 빠진) 수집의 날짜입니다.
- **처음 기록**: 거래소마다 처음 반영할 때는 그때의 목록을 상태로만 잡습니다. 선물은 상장일이 있으므로 그때 상장되어 있던 계약도 모두 신규로 적습니다. 지금의 기록은 2026-09-27 사본에서 시작하므로, 그날과 10-23 사이에 들어온 bStocks 20종과 하이퍼리퀴드 9종은 '9/27 이후'로 적혀 있습니다.
- **안전 규칙**: 목록의 수집 시각이 로그의 마지막 반영 시각과 같거나 이르면 아무것도 하지 않습니다(수집이 실패해 예전 사본을 쓴 날). 한 거래소의 목록이 비었거나 수집 오류가 적혀 있으면 그 거래소는 건너뛰고, 상장 수가 직전보다 20% 넘게(4종 이상) 줄면 이탈을 적지 않고 상태에 남겨 둡니다. 하이퍼리퀴드는 수집 오류가 난 dex 의 코인을 이탈로 보지 않습니다. 워크플로도 수집이 실패한 날에는 `tradfi-ticker-lists` 에 커밋된 목록이 `data/lists/` 보다 새로울 때만 그것으로 바꿉니다.
- **화면**: 위쪽 세 칸은 거래소별 현재 상장 수, 이번 수집(직전 수집 이후)의 신규·이탈 수, 최근 30일의 신규·이탈 수입니다. 표는 거래소·기간(7D·30D·90D·전체, 마지막 수집일 기준)·종류(주식·ETF)·'구성종목만'으로 거르고, '이 보드' 열에는 그 종목을 상위 10개에 담은 이 보드의 ETF 와 순위를 적습니다(상장 점과 같은 대조 규칙). 티커를 누르면 매트릭스의 종목 검색으로 넘어갑니다. 이 보드 종목의 신규 상장 행은 주황 바탕입니다. 같은 거래소에 같은 종목이 이미 다른 심볼로 상장되어 있었다면(하이퍼리퀴드의 다른 dex, 바이낸스의 같은 종목 두 번째 계약) '추가'로 적고 강조하지 않으며 신규 수에서 뺍니다. 다른 심볼이 남은 채 한 심볼만 빠지면 '심볼 이탈'입니다. 이 판단은 빌드가 로그의 사건을 시간순으로 다시 걸으며 하므로 로그 파일 형식은 그대로입니다.

### 한계

- GitHub 의 실행 서버는 미국에 있어 바이낸스 API 가 451 을 돌려줍니다. `tradfi-ticker-lists` 스크립트는 미러로 넘어가도록 되어 있지만, 미러까지 막히거나 저장소를 받지 못하면 `data/lists/` 의 마지막 사본을 씁니다(상장 점이 그 날짜에 멈추고, 상장 로그도 그날은 쌓이지 않습니다).
- stockanalysis.com 이 자동 수집을 막으면 구성종목 스냅샷이 빠지고 `변동 %p` 가 마지막 성공 스냅샷 기준으로 남습니다.
- DRAM 처럼 주봉이 39주 미만인 ETF 는 RRG 국면이 N/A 입니다. 시간이 지나면 자동으로 채워집니다.
- MAGS 는 스타일·집중도 행입니다. 백분위는 섹터·산업 23개 사이에 끼워 넣었을 때의 위치만 적고(다른 ETF 의 백분위는 바뀌지 않음), RRG 는 SPY 대비로 1단 그래프에 마름모로 그리되 섹터 집계·전환순 상위 3 에는 넣지 않습니다. 구성종목 표에서 이전 스냅샷에 없던 ETF 는 변동 %p 를 계산하지 않습니다.
