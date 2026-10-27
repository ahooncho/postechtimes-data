"""사이트에서 실제로 본 마크업(2026-10 기준)으로 파서를 점검합니다. 실행: python3 -m unittest discover tools/archive/tests"""
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ptimes_archive as pa

LIST = '''
<nav><a href="/news/articleList.html?sc_section_code=S1N1&view_type=sm">보도</a>
<a href="/news/articleList.html?sc_sub_section_code=S2N9&view_type=sm">TOP/준TOP</a>
<a href="/news/articleList.html?sc_sub_section_code=S2N34&view_type=sm">단신보도</a>
<a href="/news/articleList.html?sc_section_code=S1N27&view_type=sm">여론</a>
<a href="/news/articleList.html?sc_sub_section_code=S2N29&view_type=sm">형산만평</a>
<a href="/news/articleList.html?sc_section_code=S1N28&view_type=sm">Headlines</a></nav>
<section class="article-list-content type-sm text-left">
<div class="list-block"> <div class="list-image" style="background-image:url(./thumbnail/202610/24330_28609_248_v150.jpg)">
<a href="/news/articleView.html?idxno=24330" target="_top"><span class="show-for-sr">제478호 형산만평</span></a></div>
<div class="list-titles"><a href="/news/articleView.html?idxno=24330" target="_top"><strong>제478호 형산만평</strong></a></div>
<p class="list-summary"><a href="/news/articleView.html?idxno=24330"></a></p>
<div class="list-dated">형산만평 | 김민하 기자 | 2026-10-07 21:24</div> </div>
<div class="list-block"> <div class="list-image" style="background-image:url(./thumbnail/202610/24339_28615_440_v150.png)"></div>
<div class="list-titles"><a href="/news/articleView.html?idxno=24339"><strong>내년 3월 인공지능학과 신설, 그 추진 배경과 내용은?</strong></a></div>
<div class="list-dated">TOP/준TOP | 김범준 기자, 조아훈 기자 | 2026-10-07 21:36</div> </div>
<div class="list-block"><div class="list-titles"><a href="/news/articleView.html?idxno=24313"><strong>At Long Last, DAWN</strong></a></div>
<div class="list-dated">Headlines | Kim Reporter | 2026-10-07 20:45</div></div>
</section><a href="?page=2&total=38">2</a>
'''

BRIEF = '''<article id="article-view-content-div" class="article-veiw-body">
<p><strong>산업경영공학과 채민우 교수, ‘올해의 대한민국 통계연구자상’ 수상</strong></p>
<p>채민우(산경) 교수가 지난달 1일 제32회 통계의 날 기념식에서 수상했다.</p>
<p>조아훈 기자 ahooncho@</p>
<p><b>우리대학에서 즐기는 캐나다, 2026 PAF 개최</b></p>
<p>지난달 29일부터 30일 이틀간 열렸다.</p>
<p>김수진 기자 kimjinsujin@</p>
<p>저작권자 © 포항공대신문 무단전재 및 재배포 금지</p>
</article>'''

class ParserTests(unittest.TestCase):
    def test_list(self):
        items = pa.parse_list(LIST)
        self.assertEqual([i["id"] for i in items], [24330, 24339, 24313])
        self.assertEqual(items[0]["category"], "형산만평")
        self.assertEqual(items[0]["bylines"], ["김민하 기자"])
        self.assertEqual(items[0]["photo"], "https://times.postech.ac.kr/news/photo/202610/24330_28609_248.jpg")
        self.assertIsNone(items[1]["photo"])  # PNG 도표는 사진으로 쓰지 않음
        self.assertEqual(items[1]["bylines"], ["김범준 기자", "조아훈 기자"])
        self.assertEqual(items[0]["published"], "2026-10-07T21:24:00+09:00")
        self.assertEqual(pa.total_count(LIST), 38)

    def test_categories_and_classify(self):
        cats = pa.parse_categories(LIST)
        self.assertEqual(cats["TOP/준TOP"], {"code": "S2N9", "parent": "S1N1"})
        self.assertEqual(cats["형산만평"], {"code": "S2N29", "parent": "S1N27"})
        items = [pa.classify(i, cats) for i in pa.parse_list(LIST)]
        self.assertEqual(items[0]["sectionCode"], "S1N27")
        self.assertEqual(items[0]["sectionName"], "여론")
        self.assertEqual(items[1]["subSectionCode"], "S2N9")
        self.assertEqual(items[2]["edition"], "en")
        self.assertEqual(items[1]["edition"], "ko")

    def test_briefs(self):
        b = pa.parse_briefs(BRIEF)
        self.assertEqual(len(b), 2)
        self.assertEqual(b[0]["reporter"], "조아훈 기자")
        self.assertEqual(b[1]["title"], "우리대학에서 즐기는 캐나다, 2026 PAF 개최")

    def test_issue_numbering(self):
        def art(i, day, title, edition="ko", code="S1N1"):
            return {"id": i, "title": title, "published": f"2026-{day}T21:00:00+09:00", "edition": edition, "sectionCode": code}
        arts = [art(1, "09-09", "제477호 형산만평"), art(2, "09-09", "기사"),
                art(3, "10-07", "제478호 단신보도"), art(4, "10-08", "기사"),
                art(5, "09-09", "Old", "en", "S1N28"), art(6, "10-07", "New", "en", "S1N28")]
        issues = pa.build_issues(arts, {"gapDays": 3, "englishAnchor": {"number": 199, "date": "2026-10-07"}}, {})
        self.assertEqual([(i["number"], len(i["articles"])) for i in issues["ko"]], [(477, 2), (478, 2)])
        self.assertEqual([i["number"] for i in issues["en"]], [198, 199])

if __name__ == "__main__":
    unittest.main()
