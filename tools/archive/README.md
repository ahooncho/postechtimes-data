# 기사 목록 보관소 (`data/`)

앱이 학교 서버(times.postech.ac.kr)에 사용자마다 수십 번씩 요청하지 않도록, **기사 목록과 호 구성**을 이 저장소의 `data/`에 JSON으로 보관합니다. 평소에는 GitHub Actions(`Collect`)가 자동으로 돌리고, 아래 명령은 로컬에서 손으로 돌릴 때 씁니다. 앱은 이 파일로 홈·지난 호·기자 페이지를 만들고, **기사 본문만** 열 때 사이트에서 받습니다.

캠퍼스 와이파이는 학생들이 같은 IP를 쓰므로, 앱마다 사이트를 직접 긁으면 학교 방화벽이 캠퍼스 전체를 막을 수 있습니다(개발 중 실제로 403 차단을 겪었습니다).

## 처음 한 번: 전체 목록 받기

```sh
python3 tools/archive/ptimes_archive.py backfill
```

- 사이트 전체 기사 목록을 20건씩, 요청 사이 1.5초 쉬며 받습니다. 기사 본문은 받지 않습니다(단신 묶음만 꼭지를 나누려고 받습니다).
- 403/429를 받으면 바로 멈춥니다. 30분~몇 시간 뒤 **같은 명령**을 다시 실행하면 이어서 받습니다(`data/.backfill_state.json`, `data/.raw_articles.json`도 커밋해 두면 다른 기계·Actions에서도 이어집니다).
- 끝나면 `data/`의 변경을 커밋합니다.

## 자동으로: 사이트가 바뀌었을 때만 갱신 (권장)

```sh
python3 tools/archive/ptimes_archive.py watch              # 10분마다 확인, 계속 실행
python3 tools/archive/ptimes_archive.py watch --once       # 한 번만 확인(cron·작업 스케줄러용)
```

사이트의 RSS 전체기사 피드(`/rss/allArticle.xml`)는 정적 파일이라 `ETag`·`Last-Modified`가 붙고, 기사가 올라오거나 고쳐지면 바뀝니다(피드 안 `lastBuildDate`와 같은 값). 지난번 값을 같이 보내는 조건부 GET을 쓰므로, 바뀌지 않았으면 서버가 본문 없이 **304**만 돌려줍니다. 바뀌었을 때만 `update`를 돌립니다.

| | 요청 |
|---|---|
| 섹션 17개 첫 쪽을 5분마다 긁기 | 시간당 전체 페이지 약 200개 |
| `watch` (10분 간격) | 시간당 304 응답 6개(각 수백 바이트) + 호가 올라오는 날만 목록 몇 쪽 |

피드 내용은 신호로만 씁니다. 호를 올리는 날은 한 시간에 40건 가까이 올라와 피드 창(최근 수십 건)을 넘길 수 있으므로, 새 기사는 목록 페이지에서 이미 아는 기사가 나올 때까지 읽습니다.

> 사이트 보안 장치는 **HEAD 요청**을 오류 페이지(`se-cu.com/ndsoft/error.html`)로 302 리디렉션합니다. 이 도구는 GET만 쓰고, 오류 페이지로 돌려보내지면 차단으로 보고 멈춥니다(`watch`는 한 시간 쉰 뒤 다시 확인).

## 손으로: 새 호가 나올 때마다

```sh
python3 tools/archive/ptimes_archive.py update
```

목록 앞쪽에서 처음 보는 기사만 받아 추가하고 호 파일을 다시 만듭니다. 결과(`data/issues/index.json`의 맨 위 호, 기사 수)를 확인한 뒤 커밋합니다.

## 손으로 고치기

자동 규칙이 틀린 경우 `data/overrides.json`을 고친 뒤 `build`로 다시 만듭니다(네트워크 없음).

```json
{
  "excludeArticles": [24316],
  "renumber": { "ko:2024-03-06": 455 }
}
```

- `excludeArticles`: 호에서 뺄 기사 번호
- `renumber`: `판:그 호 첫 기사 등록일` → 호수

```sh
python3 tools/archive/ptimes_archive.py build
```

## 규칙

| 항목 | 규칙 |
|---|---|
| 분류 | 목록의 분류 이름(예: `TOP/준TOP`)을 사이트 메뉴에서 읽은 코드 표(`categories.json`)로 코드화. 2차 분류는 메뉴에서 바로 앞 1차 분류에 속함 |
| 판 | 1차 분류가 Headlines·News·Campus·Postech Microscope·Opinion·Feature·Culture(S1N28·9·10·24·13·14·30)면 영문, 나머지 국문 |
| 호 묶음 | 등록 시각 순으로 늘어놓고 3일(`config.json`의 `gapDays`) 넘게 벌어지면 다른 호 |
| 국문 호수 | 호 안 제목의 `제NNN호`(단신보도·형산만평 등). 없으면 앞뒤 호 사이에 하나가 빈 경우만 채우고, 나머지는 호로 보지 않음(목록·검색에는 남음) |
| 영문 호수 | CMS에 없음. Headlines가 있는 묶음만 호로 보고 기준점(`config.json`의 `englishAnchor`: 국문 제478호와 함께 나온 No. 199)에서 앞뒤로 셈 |
| 사진 | 목록 썸네일의 원본 JPEG. PNG 도표는 쓰지 않음 |

## 파일

| 파일 | 내용 |
|---|---|
| `data/articles.json` | 전체 기사(번호·제목·등록 시각·분류·바이라인·사진·판), 번호 내림차순 |
| `data/issues/index.json` | 판별 호 목록 `{ "ko": [{number, published, lastUpload, count}], "en": [...] }` |
| `data/issues/{ko,en}/{호수}.json` | 그 호의 기사들과 단신 꼭지(`briefs`) |
| `data/categories.json` | 분류 이름 → 코드 표 |
| `data/briefs.json` | 단신 묶음별 꼭지(제목·기자·문단) |
| `data/config.json`, `data/overrides.json` | 설정과 손으로 고친 것 |
| `data/.watch_state.json` | 마지막으로 본 피드의 ETag·Last-Modified, 차단 대기 시각 |
| `data/.backfill_state.json`, `data/.raw_articles.json` | backfill 이어받기 상태 |

점검: `python3 -m unittest discover tools/archive/tests`
