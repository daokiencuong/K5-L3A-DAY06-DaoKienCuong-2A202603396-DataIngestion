# Starter · PostgreSQL + pgvector

Starter PostgreSQL + pgvector cho lab cá nhân. Pipeline trong `src/` đã được hiện thực để đọc manifest HTML, chunk, gọi embedding API OpenAI-compatible, lưu/retrieve bằng pgvector và chạy benchmark. Có thể thay bằng Qdrant, Chroma hoặc vector store tương đương theo [GUIDE.md](../GUIDE.md).

## Cấu trúc

```text
starter/
├── docker-compose.yml     # PostgreSQL 16 + pgvector, chỉ mở cổng local
├── init.sql               # bật extension khi tạo database mới
├── requirements.txt       # psycopg, pypdf và python-dotenv
├── .env.example           # cấu hình mẫu; copy thành .env
├── scripts/check_setup.py # kiểm tra kết nối và extension (đã chạy được)
├── src/
│   ├── models.py          # cấu trúc dữ liệu mẫu
│   ├── ingestion.py       # đọc HTML manifest, chuẩn hóa và giữ locator
│   ├── chunking.py        # fixed-size và paragraph-aware chunking
│   ├── embeddings.py      # OpenAI-compatible embedding API
│   ├── store.py           # PostgreSQL/pgvector, cosine search và metadata filter
│   ├── evaluation.py      # Hit@1, Evidence Hit@3 và trace
│   └── run.py             # chạy A/B/C, lưu trace và thống kê runtime
├── data/                  # corpus được phép sử dụng và manifest
└── results/               # trace top-3, số liệu và audit
```

## 1. Cài và kiểm tra database

Cần Docker Desktop hoặc Docker Engine + Compose, Python **3.11–3.14**, và mạng để tải image/dependencies lần đầu. Các lệnh sau chạy từ thư mục `starter/`:

```bash
cd starter
python3 -m venv .venv
```

Kích hoạt `.venv`: macOS/Linux dùng `source .venv/bin/activate`; Windows PowerShell dùng `.venv\Scripts\Activate.ps1`. Sau đó:

```bash
python -m pip install -r requirements.txt
# macOS/Linux: cp .env.example .env
# Windows PowerShell: Copy-Item .env.example .env
docker compose -p day6_student_starter up -d --wait
python scripts/check_setup.py
```

Kỳ vọng: hiện tên database, phiên bản PostgreSQL và pgvector, sau đó `Setup OK`. Đây **chỉ là kiểm tra hạ tầng**, không phải kết quả benchmark. Docker dùng cổng `127.0.0.1:55433` và mật khẩu cố định **chỉ dùng local cho lab**; nếu cổng bận, sửa đồng thời port trong `docker-compose.yml` và `DATABASE_URL` trong `.env`. `init.sql` chỉ chạy khi volume mới được tạo; nếu database sẵn có chưa bật extension, xem log Docker và kiểm tra cấu hình thay vì xóa volume chứa dữ liệu.

Dừng container nhưng giữ dữ liệu:

```bash
docker compose -p day6_student_starter stop
```

## 2. Chọn embedding model

Điền key tương ứng trong `.env` nếu dùng OpenRouter, OpenAI hoặc Gemini; **không commit `.env`**. Model local không cần API key. `requirements.txt` chỉ có dependencies cơ bản: tự cài SDK hoặc thư viện local phù hợp với model bạn chọn và ghi lại version/lệnh cài trong báo cáo. Ghi tên model và số chiều; dùng cùng model cho toàn bộ chiến lược và vector query tương thích vector document.

## 3. Chạy pipeline

1. Đặt corpus vào `data/` hoặc cung cấp manifest/hướng dẫn truy cập. Đọc [GUIDE.md](../GUIDE.md) về **30 trang nội dung**, quyền dữ liệu và metadata.
2. Đặt corpus và manifest hợp lệ trong `data/`; pipeline kiểm tra ID, đường dẫn và độ dài nội dung.
3. Điền `EMBEDDING_MODEL` và một API key trong `.env` (ví dụ `text-embedding-3-small` cùng `OPENAI_API_KEY`). Không commit `.env`.
4. Tạo benchmark tối thiểu 20 câu, chia dev/test và kiểm chứng evidence theo [BENCHMARK.md](../BENCHMARK.md).
5. Chạy `python -m src.run` từ thư mục `starter/`. Pipeline chạy A/B/C trên cùng corpus, model và benchmark, rồi ghi trace top-3 vào `results/retrieval_results.json`.

Kết quả gồm số chunk và phân bố độ dài theo ký tự, tổng ký tự nguồn, phiên bản runtime, token usage nếu API trả về, thời gian embedding/ingest/search và các chỉ số dev/test. Không cài tokenizer riêng; token usage chỉ được ghi khi provider trả về. Pipeline nạp lại cùng dữ liệu thêm một lần bằng vector đã tạo sẵn, rồi so số dòng lưu trong DB để xác nhận không nhân đôi. `PgVectorStore.upsert` thay snapshot theo strategy trong transaction; primary key `(strategy, chunk_id)` ngăn ID trùng. Cấu hình hiện dùng exact cosine search (không tạo ANN index).

Lưu code, cấu hình, kết quả và lệnh chạy để người chấm tái lập; không commit key, dữ liệu mật hoặc dữ liệu không có quyền chia sẻ.
