# postechtimes-data

포항공대신문(국문)·The POSTECH Times(영문) **기사 목록과 호 구성**을 JSON으로 보관하는 공개 저장소입니다. 포항공대신문 앱은 이 파일로 홈·지난 호·분야·기자 페이지를 만들고, **기사 본문만** 열 때 신문사 사이트에서 받습니다.

## 왜 따로 두나

앱마다 신문사 사이트(times.postech.ac.kr)를 직접 긁으면, 캠퍼스 와이파이처럼 같은 IP를 쓰는 사용자들의 요청이 몰려 학교 방화벽이 그 IP를 통째로 막습니다(개발 중 실제로 며칠간 403 차단을 겪었습니다). 수집은 여기 한 곳에서만 하고, 앱은 이 정적 파일을 받습니다.

## 어떻게 갱신되나 — 사람 손 없이

[`Collect`](.github/workflows/collect.yml) 작업이 **15분마다** 돕니다.

1. 사이트 RSS 전체기사 피드(`/rss/allArticle.xml`)에 지난번 `ETag`·`Last-Modified`를 보내는 조건부 GET을 합니다. 바뀌지 않았으면 서버가 본문 없이 **304**만 돌려주고 끝납니다(요청 하나, 수백 바이트).
2. 바뀌었을 때만 목록 첫 쪽부터 이미 아는 기사가 나올 때까지 읽고, `data/`를 다시 만들어 커밋합니다.
3. 403·429·보안 오류 페이지 리디렉션을 받으면 한 시간 동안 요청하지 않습니다.

학교 서버로 가는 양: 평소 시간당 304 응답 4개, 호가 올라오는 날만 목록 몇 쪽.

### 손으로 돌리기

Actions 탭 → **Collect** → **Run workflow**에서 `mode`를 고릅니다.

- `backfill`: 처음 한 번. 사이트 전체 기사 목록을 1.5초 간격으로 받습니다(약 15~20분). 막히면 받은 데까지 커밋되고, 다시 실행하면 이어 받습니다.
- `update`: 새 기사를 바로 확인
- `watch`: 예약 작업과 같음

자동 규칙이 틀렸으면 `data/overrides.json`을 고치고 로컬에서 `python3 tools/archive/ptimes_archive.py build` 후 커밋합니다. 자세한 규칙과 파일 형식은 [tools/archive/README.md](tools/archive/README.md).

## 앱이 읽는 주소

```
https://ahooncho.github.io/postechtimes-data/data/issues/index.json
https://ahooncho.github.io/postechtimes-data/data/issues/ko/478.json
```

## 권리

기사·사진의 권리는 포항공대신문에 있습니다. 이 저장소는 공개된 기사 목록(제목·분류·바이라인·등록 시각·대표 사진 주소)만 담고 본문은 담지 않습니다(단신 묶음의 꼭지 나누기 제외).
