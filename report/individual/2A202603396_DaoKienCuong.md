# Báo cáo cá nhân — Day 6: Data Ingestion

**Học viên:** Đào Kiên Cường · **MSSV:** 2A202603396 · **Lớp:** L3A\
**Corpus:** Python 3.12 Tutorial and HOWTO · **Kết quả:** `starter/results/retrieval_results.json`\
**Thời điểm chạy:** 2026-11-13

## Dữ liệu và pipeline

* **Nguồn:** Python 3.12.16 Documentation, snapshot các trang Tutorial và HOWTO chính thức. Manifest ghi giấy phép PSF License Version 2 cho tài liệu và Zero-Clause BSD cho ví dụ mã; xem `starter/data/manifest.json` và trang license/copyright trong corpus.
* **Quy mô:** 30 đơn vị HTML riêng biệt (17 Tutorial, 13 HOWTO), tổng **615.033 ký tự nội dung đã chuẩn hóa** theo manifest; mỗi đơn vị có ít nhất 1.500 ký tự (min 2.431, max 53.760). Corpus HTML không có số trang vật lý; `unit_id` là locator để truy vết. Một trang HTML độc lập được tính là một đơn vị nguồn.
* **Trích xuất và chuẩn hóa:** chỉ lấy phần Sphinx `div[role=main].body`; bỏ navigation/script/style, giải mã entity HTML, chuẩn hóa Unicode NFC, xuống dòng và khoảng trắng. Ingestion kiểm tra manifest, đường dẫn trong thư mục dữ liệu, ID trùng và độ dài tối thiểu. Chạy thành công với 30 đơn vị. Giới hạn: bộ trích xuất không OCR; chỉ phù hợp với snapshot HTML đã chọn.
* **Embedding:** OpenAI `text-embedding-3-small`, 1.536 chiều, cùng model cho tài liệu và query.
* **Môi trường:** Python 3.11.9 (metadata của virtualenv); psycopg 3.3.8, psycopg-binary 3.3.8, python-dotenv 1.2.4 và pypdf 6.21.4. Embedding gọi OpenAI-compatible API qua `urllib` của Python, không dùng SDK OpenAI.
* **Vector store:** PostgreSQL 16 với pgvector 0.8.7, cosine distance; Docker Compose local. Search dùng `ORDER BY embedding <=> query` và không tạo ANN index, nên là exact cosine scan. Lệnh chạy từ `starter/`: `python -m src.run`.
* **Metadata:** `document_id`, `source`, `unit_id`, `section`, `title`, `chunk_index`, offsets ký tự và chiến lược chunk. `section` lấy trực tiếp từ manifest với giá trị `tutorial` hoặc `howto`, dùng làm filter.
* **Nạp lại:** `upsert` xóa snapshot theo strategy rồi chèn lại trong cùng transaction; khóa chính `(strategy, chunk_id)` chặn ID trùng. Đây là bảo đảm theo thiết kế/code. Chưa xác minh bằng lần nạp lại thực tế trong phiên rà soát này vì Docker daemon không chạy.
* **Chi phí và thời gian:** theo số liệu OpenAI bạn cung cấp, chi phí API embedding của lượt chạy là **\$0.01 USD**. Trace gốc ghi 39,626 giây embedding và 4,779 giây ingest database; không lưu token usage hoặc thời gian search. Adapter đã được cập nhật để lưu provider-reported input tokens và thời gian search cho lượt chạy kế tiếp.

## Cấu hình và benchmark

| Chiến lược             | Chunking                                            | Metadata/filter                               |
| ---------------------- | --------------------------------------------------- | --------------------------------------------- |
| A — fixed              | 500 ký tự, overlap 50                               | Có locator và `section`; không filter         |
| B — paragraph          | Gom đoạn đến 900 ký tự, chỉ chia đoạn vượt giới hạn | Cùng metadata; không filter                   |
| C — paragraph + filter | Giống hệt B; dùng chung chunks/vector với B         | Áp dụng `section` khi câu hỏi cung cấp filter |

B–C chỉ thay đổi cách truy xuất; A–B thay đổi chunking. Benchmark cố định có 20 câu, 15 dev/5 test, trong đó 4 câu có metadata filter. Câu hỏi, gold source/page và evidence quote lưu tại `benchmark/python_docs_questions.json`.

Quy tắc so sánh cấu hình trong phần phân tích này ưu tiên Hit\@1 trên dev, sau đó xét coherence và tài nguyên; với câu hỏi yêu cầu phạm vi thì dùng filter metadata. Theo số liệu dev, B/C đồng hạng Hit\@1 (4/15), còn A có Evidence Hit\@3 cao hơn (7/15 so với 6/15). Vì vậy dev chưa cho một cấu hình thắng tuyệt đối. Quy tắc này được nêu khi tổng hợp báo cáo, không phải tiêu chí tiền đăng ký trước lần chạy.

Các điểm test được giữ để báo cáo kết quả đã quan sát, không dùng làm tiêu chí chọn cấu hình. Tuy nhiên, điểm test đã được xem trong quá trình hoàn thiện báo cáo; do đó tập này chỉ mang tính thăm dò và không được gọi là holdout độc lập. Muốn có đánh giá holdout độc lập cần tạo benchmark test mới, khóa cấu hình trước khi chạy và không dùng kết quả test để điều chỉnh.

## Kết quả retrieval

| Chiến lược             | Split |       Hit\@1 | Evidence Hit\@3 | Số chunks | Thời gian embedding / ingest |
| ---------------------- | ----: | -----------: | --------------: | --------: | ---------------------------: |
| A — fixed              |   Dev | 2/15 (13,3%) |    7/15 (46,7%) |     1.334 |             39,626s / 4,779s |
| A — fixed              |  Test |    1/5 (20%) |       3/5 (60%) |     1.334 |             39,626s / 4,779s |
| B — paragraph          |   Dev | 4/15 (26,7%) |      6/15 (40%) |       781 |             39,626s / 4,779s |
| B — paragraph          |  Test |    2/5 (40%) |       4/5 (80%) |       781 |             39,626s / 4,779s |
| C — paragraph + filter |   Dev | 4/15 (26,7%) |      6/15 (40%) |       781 |             39,626s / 4,779s |
| C — paragraph + filter |  Test |    2/5 (40%) |       4/5 (80%) |       781 |             39,626s / 4,779s |

Thời gian hiện có là thời gian chung của đợt embedding và ingest; pipeline chưa đo riêng thời gian từng chiến lược hoặc query search. Chi phí API **\$0.01 USD**.

Trace hiện có lưu số chunk (A: 1.334; B/C: 781), nhưng lượt chạy ban đầu chưa ghi phân bố độ dài chunk hoặc tổng ký tự chunk. `run.py` đã được cập nhật để các lượt chạy sau ghi min/mean/p50/p90/max ký tự mỗi chunk, tổng ký tự nguồn, phiên bản runtime, provider-reported input tokens và thời gian search; các thống kê chưa có trong trace hiện tại không được suy đoán. Tokenizer riêng chưa được chọn nên token count không có nếu provider không trả usage.

## Đánh giá thủ công

### Coherence

Đã xem 10 vị trí nguồn chung giữa kết quả A và B, đối chiếu đúng chunks bao phủ cùng offsets; C dùng nguyên chunks của B. Điểm: **A 1,0/2 (10 vị trí), B 2,0/2, C 2,0/2**. Ở các mẫu này, fixed-size thường cắt đầu/cuối câu hoặc ví dụ; chunk theo đoạn giữ được đoạn giải thích cùng ngữ cảnh. Bảng từng mẫu và lý do ở `starter/results/manual_review_coherence.csv`.

Đây là mẫu chẩn đoán chọn sau lần chạy, lấy từ các kết quả có overlap giữa A và B; chưa phải mẫu coherence tiền đăng ký từ corpus. Có thiên lệch về vùng được truy xuất, nên điểm này cần được xem là đánh giá sơ bộ, không phải ước lượng không thiên lệch cho toàn corpus.

### Metadata và filter

* Kiểm tra 180/180 hit top‑3: `source`, `unit_id` và `section` khớp manifest; `unit_id` là locator tương đương page cho HTML.
* Cả 4 câu có filter đều trả về top‑3 tuân thủ section: **12/12 hit**.
* Top‑3 của C không đổi so với B ở cả 4 câu filter (q03, q10, q18, q19). Filter đúng và hữu ích như một ràng buộc phạm vi, nhưng benchmark này không cho thấy cải thiện retrieval vì baseline B vốn đã lấy đúng section.

### Grounding trên 5 câu test

| Chiến lược             | Điểm 0 | Điểm 1 | Điểm 2 | Trung bình /2 |
| ---------------------- | -----: | -----: | -----: | ------------: |
| A — fixed              |      1 |      1 |      3 |           1,4 |
| B — paragraph          |      0 |      0 |      5 |           2,0 |
| C — paragraph + filter |      0 |      0 |      5 |           2,0 |

Nhãn từng câu và lý do ở `starter/results/manual_review_grounding.csv`. Đã spot-check 15/15 nhãn (5 câu test × 3 cấu hình) bằng Codex hỗ trợ AI, đối chiếu câu hỏi, đáp án kỳ vọng, nội dung chunk top‑3 và locator nguồn; ghi chú từng trường hợp nằm trong CSV.&#x20;

q19 có gold chính tại `tutorial-errors`. B/C truy xuất được bằng chứng tương đương tại `tutorial-controlflow`: “a try statement’s else clause runs when no exception occurs”, đủ trả lời và truy nguồn. Đã bổ sung passage này vào `acceptable_gold` trong benchmark. Alternative được ghi nhận sau khi xem test, nên giữ nguyên các chỉ số exact-gold ban đầu (B/C Evidence Hit\@3 = 4/5) và chỉ báo cáo riêng phân tích bổ sung: khi chấp nhận gold alternative, B/C đạt 5/5 trên test. Không xem đây là kết quả holdout độc lập.

## Phân tích và kết luận

* **A–B:** B giảm số chunks từ 1.334 xuống 781 (41,5%). B tốt hơn A trên test: Hit\@1 tăng từ 1/5 lên 2/5, Evidence Hit\@3 từ 3/5 lên 4/5; dev cho kết quả hỗn hợp (A có Evidence Hit\@3 7/15, B 6/15). Mẫu coherence sơ bộ cũng nghiêng về B.
* **B–C:** Hit\@1, Evidence Hit\@3 và top‑3 trên các câu filter giống nhau. C không tăng điểm trong benchmark này; filter vẫn đảm bảo phạm vi `section` khi được yêu cầu.
* **Ví dụ lỗi:** q17 strategy A không đưa được bằng chứng về `finally` vào top‑3; B/C có đoạn trực tiếp và đạt Evidence Hit\@3. q19 B/C bị metric exact-gold ban đầu đánh dấu trượt dù top‑3 có evidence tương đương ở `tutorial-controlflow`; alternative đã được thêm sau lần xem test và chỉ dùng trong phân tích bổ sung. q20 A chỉ đưa ví dụ liên quan IPv4/IPv6 nhưng chưa chứng minh rõ factory xử lý phiên bản tự động; B/C lấy được đoạn giải thích phù hợp.
* **Lựa chọn theo dev:** B/C đồng hạng Hit\@1, nhưng A có Evidence Hit\@3 cao hơn. Nếu cần truy xuất đúng ngay ở top‑1, dùng B làm mặc định; dùng C khi câu hỏi yêu cầu giới hạn theo `section`, vì C thực thi filter mà không thêm chi phí tạo/lưu vector so với B. Kết quả hiện tại chưa chứng minh C tăng retrieval accuracy.
* **Giới hạn:** test chỉ có 5 câu và alternative q19 được thêm sau khi xem test, nên kết quả vẫn mang tính thăm dò, không phải holdout độc lập; kết quả dao động mạnh theo từng câu. Coherence sample chọn sau chạy và grounding spot-check có hỗ trợ AI. Trace cũ thiếu phân bố độ dài chunk, token usage và thời gian search; code mới sẽ lưu thống kê chunk/search và token usage nếu provider trả về. Chưa đánh giá câu trả lời do LLM tạo; đây chỉ là chất lượng evidence retrieval.
* **Làm lại:** tiền đăng ký 10 vị trí coherence trước khi chạy; xác nhận alternatives trước thử nghiệm; lưu token usage và các thống kê pipeline; kiểm tra nạp lại trên PostgreSQL đang chạy; mở rộng test độc lập, không dùng test để điều chỉnh chiến lược.

## Tệp minh chứng

* Full trace và top‑3: `starter/results/retrieval_results.json`
* Nhãn grounding: `starter/results/manual_review_grounding.csv`
* Mẫu coherence: `starter/results/manual_review_coherence.csv`
* Experiment log: `artifacts/experiment_log.csv`
* Manifest dữ liệu: `starter/data/manifest.json`

