# 미국 섹터·산업 ETF 상대 강도 데이터 (RRG)

미국 섹터·산업 ETF의 상대 강도를 매주 계산하기 위한 원본 데이터를 모으는 저장소입니다. (1) SPY 대비 상대수익률 백분위, (2) ETF÷SPY·ETF÷QQQ 비율선, (3) RRG 1단(섹터÷SPY)과 2단(산업·테마÷상위 섹터 ETF)의 RS-Ratio·RS-Momentum은 여기서 계산하지 않습니다. 이 저장소의 스크립트는 일봉 종가와 구성종목 스냅샷을 정확하게 받아서 저장하는 일만 합니다.

스크립트는 [tradfi-ticker-lists](https://github.com/tlzkrh1029/tradfi-ticker-lists)의 `fetch_tradfi_tickers.py`와 같은 규칙을 따릅니다. 표준 라이브러리만 사용하고, 결과는 `output/`에 UTF-8 BOM CSV로 저장하며, 실행할 때마다 전체를 새로 받아 덮어씁니다.

## 가격 수집

### 결론

`fetch_prices.py`는 벤치마크 2개, 섹터 ETF 11개, 산업·테마 ETF 12개의 일봉을 API 키 없는 공개 출처에서 받아, SPY의 거래일을 기준 날짜 축으로 삼아 한 표에 정렬합니다. 같은 실행에서 각 ETF의 구성종목 상위 10개도 받아 날짜별 파일로 남깁니다.

```
python3 fetch_prices.py                  # 가격 + 구성종목, output/ 에 저장 (약 2분)
python3 fetch_prices.py --no-holdings    # 가격만
python3 fetch_prices.py --out <폴더>     # 저장 위치 변경
python3 fetch_prices.py --source yahoo   # 야후만 사용 (조정 종가를 한 출처로 통일할 때)
python3 fetch_prices.py --days 400       # 기준 축에 남길 거래일 수 변경 (기본 500)
```

종료 코드는 0(정상), 1(가격 수집에 실패한 티커가 하나라도 있음), 2(가격은 정상이지만 구성종목 일부 실패)입니다. 실행 중에는 티커마다 한 줄씩 출처와 받은 행 수를 출력하고, 마지막에 `warnings`를 표준 오류로 출력합니다. 2026-10-09 실행은 0으로 끝났고 경고는 없었습니다.

### 수집 대상

티커 목록은 스크립트 상단의 `BENCHMARKS`, `SECTORS`, `INDUSTRIES`에 있으며, 산업·테마 ETF의 상위 섹터는 `PARENT_SECTOR` 표에 있습니다. 티커를 추가하려면 이 목록만 고치면 됩니다. CSV의 열 순서와 행 순서는 이 목록 순서를 따릅니다.

| 구분 | 티커 | 2단 RRG 벤치마크(상위 섹터) |
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

### 출력 파일

`output/`에 저장하며, Excel에서 한글이 깨지지 않도록 UTF-8 BOM을 붙입니다. 가격은 소수점 넷째 자리까지, 거래량은 정수로 적습니다.

| 파일 | 형식 | 열 |
|---|---|---|
| `prices.csv` | long. 날짜 오름차순, 같은 날짜 안에서는 수집 대상 순서 | `date`, `ticker`, `open`, `high`, `low`, `close`, `adj_close`, `volume` |
| `prices_wide.csv` | wide. 행은 SPY 기준 축의 날짜, 열은 티커별 `adj_close`. 이 파일 하나로 비율 계산이 됩니다 | `date`, `SPY`, `QQQ`, `XLK`, …, `DRAM` |
| `prices_summary.json` | 실행 결과 요약 | `generated_at`(UTC), `source_order`, 티커별 `{source, adj_close_available, first_date, last_date, rows, missing_days, gap_days, off_axis_dates, partial_bar_dropped, attempts}`, `base_axis {ticker, first_date, last_date, days}`, `failed_tickers`, `parent_sector`, `holdings`, `holdings_failed`, `checks`, `warnings` |
| `holdings/YYYY-MM-DD.csv` | 구성종목 상위 10개 스냅샷. 파일명은 실행일이며 같은 날짜 파일은 덮어씁니다 | `etf`, `rank`, `symbol`, `name`, `weight_pct` |

`prices_wide.csv`의 빈 칸은 그 날짜에 그 티커의 데이터가 없다는 뜻입니다. 직전 값으로 채우지 않으므로, 비율을 계산할 때 빈 칸은 건너뛰어야 합니다.

### 날짜 축 규칙

모든 티커의 종가가 같은 날짜 축에 놓여야 산업·테마 ETF ÷ 상위 섹터 ETF를 날짜별로 나눌 수 있습니다. 그래서 다음 규칙으로 정렬합니다.

- SPY의 거래일 중 최근 500일(`--days`)이 기준 축입니다. `base_axis`에 축의 첫 날짜, 마지막 날짜, 길이를 적습니다.
- 어떤 티커에 축의 날짜 데이터가 없으면 그 칸은 빈 값으로 둡니다. 빈 칸 수는 티커별 `missing_days`에 적고, 상장 이후 구간에서만 센 빈 칸 수는 `gap_days`에 따로 적습니다. 2026-04-02에 상장한 DRAM은 `missing_days`가 369이지만 `gap_days`는 0입니다.
- 축에 없는 날짜에만 데이터가 있는 티커가 있으면 그 날짜는 버리고 `off_axis_dates`와 `warnings`에 적습니다.
- 축보다 앞선 날짜의 데이터는 조용히 버리고 `rows_before_axis`에 개수만 적습니다.
- 뉴욕 시간 16:15 이전에 실행하면 그날의 봉은 아직 장중 값이므로 버리고 `partial_bar_dropped`에 날짜를 적습니다.

스크립트는 마지막에 다음을 스스로 확인해서 `checks`에 적고, 어긋나면 `warnings`에 문장으로 남깁니다.

| 항목 | `checks` 키 |
|---|---|
| `prices_wide.csv`의 행 수가 `base_axis.days`와 같은가 | `wide_rows_equal_base_axis_days` |
| SPY, QQQ, 섹터 11개의 `missing_days`가 5 이하인가 | `core_missing_days_over_limit` (넘은 티커와 개수) |
| 모든 티커의 `last_date`가 SPY의 `last_date`와 같은가 | `last_date_differs_from_base` (다른 티커와 날짜) |
| 조정 종가가 없는 출처로 받은 티커가 있는가 | `tickers_without_adj_close` |

### 출처와 한계

가격은 티커마다 1순위 Stooq 일봉 CSV(`https://stooq.com/q/d/l/?s=spy.us&i=d`), 2순위 야후 파이낸스 chart 엔드포인트(`https://query1.finance.yahoo.com/v8/finance/chart/SPY?range=2y&interval=1d`) 순서로 시도하고, 실제로 쓴 출처를 `source`에, 실패한 시도의 원인을 `attempts`에 적습니다.

- **Stooq에는 조정 종가 열이 따로 없습니다.** Stooq에서 받은 티커는 `close`를 `adj_close`에 복사하고 `adj_close_available`을 `false`로 둡니다. 야후에서 받은 티커의 `adj_close`는 배당·분할 조정 종가입니다. 한 실행 안에서 두 종류가 섞이면 비율선이 배당만큼 어긋나므로 `warnings`에 기록합니다. 이때는 `--source yahoo`로 다시 실행하면 전 티커가 같은 기준이 됩니다.
- Stooq는 일일 요청 한도를 넘으면 CSV 대신 안내 문구를 돌려주며, 연결이 아예 되지 않는 환경도 있습니다. 연결 수준의 실패가 3번 이어지면 남은 티커에서는 Stooq를 건너뛰고(`attempts`에 `skipped`로 표시) 바로 야후로 갑니다. 저장소의 2026-10-09 결과물을 만든 환경에서는 Stooq에 연결할 수 없어서(TLS 연결이 끊기거나 CSV 대신 HTML 페이지가 옴) 25개 티커 모두 야후에서 받았습니다.
- 야후 chart 엔드포인트는 공식 지원 대상이 아니며, User-Agent가 없으면 거부합니다. HTTP 429를 받으면 기다렸다가 다시 시도하고, `query1`이 실패하면 `query2`로 넘어갑니다. 조정 종가는 배당이 지급될 때마다 과거 값이 전부 다시 계산되므로, 일부만 이어 붙이지 말고 매번 전체를 다시 받아야 합니다. 이 스크립트는 그렇게 동작합니다.
- 기본 축 길이 500 거래일을 받기 위해 야후에서는 5년치를 요청하고 축 밖의 과거 데이터는 버립니다. 단일 종목이 아닌 ETF만 다루므로 상장폐지·티커 변경은 고려하지 않습니다.
- DRAM은 2026년 4월 상장이라 2026-10-09 기준 131개 봉만 있습니다. 상장 전 구간은 빈 칸으로 두며 오류나 경고로 다루지 않습니다.
- 구성종목은 stockanalysis.com의 ETF holdings 페이지(`https://stockanalysis.com/etf/smh/holdings/`)에서 첫 번째 표를 읽습니다. 이 사이트는 브라우저처럼 보이는 Accept 헤더가 없으면 403을 돌려주고, 자동 수집을 막을 수 있습니다. 한 ETF가 막히면 그 ETF만 건너뛰고 `holdings_failed`에 적으며 종료 코드는 2가 됩니다. 전부 막히면 운용사 공식 페이지의 CSV로 출처를 바꾸는 편이 낫습니다.
- 구성종목의 `symbol`은 페이지에 적힌 그대로입니다. 해외 상장 종목은 `TSX: NXE`, `KRX: 005930`처럼 거래소가 붙고, DRAM처럼 스왑으로 노출을 얻는 ETF는 스왑 계약 식별자가 들어갑니다. 비중은 페이지 갱신 시점의 값이며 당일 종가 기준이 아닐 수 있습니다.
- 결과 CSV는 2026-10-09 실행 스냅샷(기준 축 2024-10-10 ~ 2026-10-08)입니다. 매주 보드를 갱신하려면 스크립트를 다시 실행해야 합니다.
