# GOS Academy — AI Teaching Assistant

Trợ giảng AI sử dụng **Retrieval-Augmented Generation (RAG)** để tra cứu học liệu theo lớp, trả lời kèm trích dẫn và chuyển câu hỏi chưa đủ bằng chứng cho giảng viên. Giảng viên có thể xử lý ticket, duyệt câu trả lời và đề xuất FAQ để một người khác kiểm duyệt trước khi đưa vào kho tri thức.

Dự án cung cấp API FastAPI, trang quản trị demo và Docker Compose chạy đầy đủ các tiến trình. PostgreSQL đảm nhiệm lưu trữ, tìm kiếm vector, tìm kiếm từ khóa và hàng đợi; hệ thống không cần Redis hay Celery.

> **Phạm vi hiện tại:** MVP với học liệu giả lập, webhook mô phỏng Zalo và hộp thư nhận tin cục bộ. `accepted` nghĩa là sự kiện đã được lưu; `SENT` nghĩa là hộp thư mock đã nhận. Tích hợp Zalo thật và vận hành production cần được triển khai, đánh giá riêng.

## Mục lục

- [Chức năng và công nghệ](#chuc-nang)
- [Kiến trúc hệ thống](#kien-truc)
- [Clone và chạy bằng Docker](#cai-dat)
- [Dùng thử và kiểm tra hệ thống](#dung-thu)
- [Cấu hình môi trường](#cau-hinh)
- [Quản lý học liệu](#hoc-lieu)
- [Giảng viên và quy trình FAQ](#giang-vien)
- [Vận hành và dữ liệu](#van-hanh)
- [Bàn giao sang máy khác](#ban-giao)
- [Phát triển bằng Python trên máy host](#phat-trien)
- [Kiểm thử](#kiem-thu)
- [Xử lý sự cố](#su-co)
- [Cấu trúc mã nguồn và tài liệu](#tai-lieu)

<a id="chuc-nang"></a>
## Chức năng và công nghệ

- Tiếp nhận webhook có xác thực, kiểm tra nhóm/lớp, chống trùng sự kiện và giới hạn tần suất.
- Nhập học liệu DOCX, PDF có văn bản và XLSX qua manifest kiểm tra checksum, phạm vi lớp và trạng thái duyệt.
- Tìm kiếm kết hợp vector ngữ nghĩa và PostgreSQL full-text search, hợp nhất bằng Reciprocal Rank Fusion (RRF).
- Chỉ trả lời từ nguồn đang có hiệu lực và đủ bằng chứng; câu trả lời giữ nguyên trích dẫn nguồn. Trường hợp thiếu bằng chứng, mơ hồ hoặc liên quan chính sách được chuyển giảng viên.
- Xử lý câu hỏi tiếp nối trong cùng người gửi, nhóm và lớp, với cửa sổ tối đa 20 phút và 5 lượt. Lịch sử hỗ trợ xác định chủ đề; bằng chứng vẫn phải được truy xuất lại.
- Xử lý bất đồng bộ với lease, fencing token, retry có giới hạn và transactional outbox.
- Phân quyền nhân sự theo lớp; duyệt phản hồi và xuất bản FAQ là các quyết định riêng biệt, có lưu vết.

| Thành phần | Công nghệ / vai trò |
| --- | --- |
| API và giao diện demo | Python 3.11, FastAPI, Uvicorn; HTML/CSS/JavaScript được phục vụ cùng API |
| Truy cập dữ liệu | SQLAlchemy, Psycopg, Alembic |
| Database và hàng đợi | PostgreSQL 17, pgvector, PostgreSQL full-text search |
| Embedding | `intfloat/multilingual-e5-small`, 384 chiều, chạy CPU |
| Sinh câu trả lời | `fake` chọn trích dẫn theo quy tắc để demo offline; `gemini` gọi API bên ngoài |
| Đóng gói | Docker, Docker Compose; image Python được ghim digest, dependency được khóa trong [requirements-docker.lock](requirements-docker.lock) |
| Kiểm thử | Pytest, PostgreSQL integration tests, Ruff và Docker smoke test |

Model embedding được cố định tại revision `614241f622f53c4eeff9890bdc4f31cfecc418b3`. Docker image chứa sẵn model; runtime không tự đổi model hoặc âm thầm tải một model dự phòng.

<a id="kien-truc"></a>
## Kiến trúc hệ thống

Ứng dụng được tổ chức theo module trong một codebase, dùng chung image nhưng chạy API và các worker ở những container riêng.

```mermaid
flowchart TD
    Student["Học viên / webhook mock"] --> API["FastAPI: xác thực, binding, chống trùng, quota"]
    Console["Trang quản trị demo"] --> API
    API --> DB[("PostgreSQL 17 + pgvector")]

    DB --> Events["Event worker"]
    Events --> Retrieval["Hybrid retrieval theo lớp"]
    DB --> Retrieval
    Retrieval --> Gate["Evidence gate"]
    Gate -->|Đủ bằng chứng| Generation["Fake / Gemini + validator"]
    Gate -->|Thiếu bằng chứng| Decision
    Generation --> Decision["Answer hoặc Ticket + outbox"]
    Decision --> DB

    DB --> Delivery["Delivery worker"]
    Delivery --> Inbox["Hộp thư mock trong PostgreSQL"]

    Staff["Giảng viên / Reviewer / Operator"] --> API
    API --> Human["Ticket, phản hồi được duyệt, FAQ"]
    Human --> DB
    DB --> Teacher["Teacher reply worker"]
    Teacher --> Inbox

    Sources["Học liệu + manifest"] --> Ingestion["Parser, chunking, E5, KnowledgeStore"]
    Human -->|FAQ đã được duyệt và yêu cầu xuất bản| Ingestion
    Ingestion --> DB
```

### Các service trong Docker Compose

| Service | Trách nhiệm | Trạng thái bình thường |
| --- | --- | --- |
| `db` | Lưu dữ liệu nghiệp vụ, vector, hàng đợi, outbox và inbox mock | `Up (healthy)` |
| `bootstrap` | Kiểm tra manifest/model, chạy migration, seed lớp và nhập tài liệu còn thiếu | `Exited (0)` |
| `api` | Webhook, API nội bộ, Swagger và trang quản trị demo | `Up (healthy)` |
| `event-worker` | Đọc sự kiện, truy xuất bằng chứng, tạo Answer hoặc Ticket cùng outbox | `Up` |
| `delivery-worker` | Gửi phản hồi AI/xác nhận handoff vào inbox mock | `Up` |
| `teacher-reply-worker` | Gửi phản hồi giảng viên đã được duyệt qua outbox riêng | `Up` |
| `maintenance` | Dọn các cửa sổ quota hết hạn theo chu kỳ | `Up` |

`bootstrap` chờ database healthy; API và các worker chờ bootstrap hoàn tất thành công. Khi chạy lại, bootstrap giữ nguyên tài liệu đã `READY` hoặc `REVOKED`, đồng thời cho phép thử lại lần nhập ban đầu bị lỗi. Thay đổi nội dung đã nhập cần được thực hiện bằng CLI quản trị.

Phạm vi lớp luôn được xác định từ binding đã xác minh trong database. Client không được chọn `class_id` để truy cập lớp khác. Answer hoặc Ticket được ghi cùng outbox trong một giao dịch; worker giao tin kiểm tra lại điều kiện gửi. Cơ chế này bảo vệ tính nhất quán nội bộ, không phải cam kết gửi mạng đúng một lần tới Zalo.

<a id="cai-dat"></a>
## Clone và chạy bằng Docker

### 1. Chuẩn bị máy

Cài Git và Docker Desktop hoặc Docker Engine có Compose v2, chạy **Linux containers**. Compose cần hỗ trợ `--wait` và `--wait-timeout`; quá trình đóng gói đã được kiểm tra với Compose v2.40.3 trên Docker Desktop.

```console
git --version
docker version
docker compose version
```

Docker daemon phải đang chạy. Lần build đầu cần Internet để tải base image, dependency CPU và model E5. Máy host không cần Python, PostgreSQL hoặc GPU. Bản image đã kiểm tra chạy Linux/amd64; gói image offline cần dùng cùng kiến trúc CPU với máy tạo gói.

### 2. Clone và chọn phiên bản

```console
git clone https://github.com/ManhT005/ai-teaching-assistant.git
cd ai-teaching-assistant
```

Dùng nhánh/tag được maintainer bàn giao và kiểm tra có `Dockerfile`, `compose.yaml`, `.env.example`. Nếu bản Docker được bàn giao qua nhánh `feat/docker-full-stack`, chuyển sang nhánh đó **sau khi nhánh đã được công bố trên remote**:

```console
git fetch origin
git switch feat/docker-full-stack
```

Nếu repository yêu cầu quyền truy cập, xác thực GitHub bằng tài khoản có quyền trước khi clone. Tất cả lệnh bên dưới chạy từ thư mục gốc repository.

### 3. Tạo cấu hình

**Windows — PowerShell:**

```powershell
.\scripts\setup-docker.ps1
```

Script sao chép `.env.example` sang `.env` và tạo ba giá trị ngẫu nhiên độc lập cho `POSTGRES_PASSWORD`, `MOCK_WEBHOOK_TOKEN`, `OPERATOR_API_TOKEN`. Script từ chối ghi đè `.env` đã có. Nếu máy chặn thực thi script, có thể sao chép file mẫu và điền ba giá trị này thủ công.

**Linux / macOS — Bash:**

```bash
# Chỉ tạo file nếu chưa có cấu hình cũ.
if [ ! -e .env ]; then cp .env.example .env; fi

# Chạy ba lần, lấy mỗi kết quả cho một secret riêng.
openssl rand -hex 32
openssl rand -hex 32
openssl rand -hex 32
```

Mở `.env`, điền lần lượt ba secret trên. Hai API token phải khác nhau và dài tối thiểu 32 ký tự. Giữ `LLM_PROVIDER=fake` cho lần chạy đầu. `.env` chứa thông tin riêng của máy và đã được loại khỏi Git.

Nếu đã có `.env`, đối chiếu với [.env.example](.env.example), bổ sung biến mới và giữ mỗi tên biến đúng một lần. Không thay mật khẩu database đang có dữ liệu chỉ bằng cách sửa file cấu hình.

### 4. Build và khởi động toàn bộ

```console
docker compose config --quiet
docker compose up -d --build --wait --wait-timeout 180
docker compose ps -a
```

Lệnh đầu kiểm tra cấu hình mà không in secret. Lần build đầu có thể mất nhiều phút; `--wait-timeout 180` là thời gian chờ service sẵn sàng sau bước build, không giới hạn thời gian tải model/build image.

Đối chiếu trạng thái với bảng service phía trên. `bootstrap` kết thúc với mã `0` là trạng thái mong đợi. Nếu có lỗi, xem [xử lý sự cố](#su-co) trước khi tiếp tục.

### 5. Mở ứng dụng

| Địa chỉ mặc định | Mục đích |
| --- | --- |
| [http://127.0.0.1:8000/demo/admin](http://127.0.0.1:8000/demo/admin) | Trang quản trị demo |
| [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) | Swagger / thử API |
| [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health) | Kiểm tra tiến trình API |
| [http://127.0.0.1:8000/ready](http://127.0.0.1:8000/ready) | Kiểm tra kết nối database và extension pgvector |

API mặc định bind localhost. Nếu đổi `API_PORT`, cập nhật cổng trong các URL. `/ready` chưa xác nhận toàn bộ luồng hỏi–đáp; cần chạy kiểm tra bên dưới.

<a id="dung-thu"></a>
## Dùng thử và kiểm tra hệ thống

### Kiểm tra luồng hoàn chỉnh

Với học liệu demo mặc định và `LLM_PROVIDER=fake`:

```console
docker compose exec api python -m scripts.smoke_docker
```

Lệnh tạo các sự kiện giả lập và kiểm tra: xác thực operator, tiếp nhận/chống trùng, câu trả lời có trích dẫn, handoff tạo ticket và giao tin mock qua các worker đang chạy. Thành công trả JSON có `"docker_smoke": "passed"`. Lệnh ghi thêm sự kiện/ticket vào instance được kiểm tra; không gọi Gemini hoặc gửi Zalo thật.

### Trang quản trị demo

1. Mở `/demo/admin`, tab **Vận hành**. Nhập `MOCK_WEBHOOK_TOKEN` và `OPERATOR_API_TOKEN` từ `.env`.
2. Chọn nhóm demo, gửi câu hỏi rồi làm mới inbox sau khi worker xử lý.
3. Dùng tab **Ticket giảng viên** và **Duyệt FAQ** sau khi đã [cấp credential nhân sự](#giang-vien).

Hai binding demo được seed sẵn:

| Nhóm mock | Mã lớp |
| --- | --- |
| `mock-google-sheets` | `GS-KPI-01` |
| `mock-ai-automation` | `AUTO-N8N-01` |

Ví dụ câu hỏi: `/hoi Lỗi HTTP 401 trong n8n cần kiểm tra gì?`. Câu hỏi về giảm học phí sẽ được chuyển giảng viên. Giao diện chỉ giữ token trong phiên trang hiện tại và xóa khi reload; phân quyền vẫn được kiểm tra tại API.

### Gọi webhook trực tiếp bằng PowerShell

```powershell
$webhookToken = "THAY_BANG_MOCK_WEBHOOK_TOKEN_TRONG_ENV"
$body = @{
    event_id = "demo-" + [Guid]::NewGuid().ToString("N")
    channel = "mock"
    group_id = "mock-ai-automation"
    sender_id = "student-demo"
    text = "/hoi Lỗi HTTP 401 trong n8n cần kiểm tra gì?"
    received_at = [DateTimeOffset]::Now.ToString("o")
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/webhook/mock `
    -Headers @{ "X-Webhook-Token" = $webhookToken } `
    -ContentType "application/json; charset=utf-8" `
    -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

Gửi lại cùng body để nhận `duplicate`; dùng `event_id` mới để tạo sự kiện mới. Nhóm không được cấp quyền trả `403`; trường ngoài schema hoặc `class_id` do client thêm trả `422`. `received_at` phải có múi giờ. Tin bot, câu không bắt đầu bằng lệnh `/hoi` hoặc câu hỏi trống được bỏ qua. Quota mặc định là 10 sự kiện mới/người gửi/phút và 100 sự kiện mới/nhóm/phút; vượt quota trả `429` cùng `Retry-After`.

API quan sát dùng header `X-Operator-Token`:

```text
GET /internal/demo/metrics
GET /internal/demo/events/{internal_event_id}
GET /internal/demo/deliveries?limit=20
```

`internal_event_id` là ID số trong database, khác `event_id` do webhook gửi lên.

<a id="cau-hinh"></a>
## Cấu hình môi trường

[.env.example](.env.example) là file mẫu đầy đủ. Các biến thường cần thay đổi:

| Biến | Mặc định / yêu cầu |
| --- | --- |
| `POSTGRES_DB`, `POSTGRES_USER` | `gos_academy`, `gos` |
| `POSTGRES_PASSWORD` | Bắt buộc; khởi tạo mật khẩu của database mới |
| `MOCK_WEBHOOK_TOKEN`, `OPERATOR_API_TOKEN` | Bắt buộc; hai secret độc lập, tối thiểu 32 ký tự |
| `POSTGRES_PORT` | Cổng PostgreSQL trên host: `5432` |
| `API_BIND_HOST`, `API_PORT` | `127.0.0.1`, `8000` |
| `APP_IMAGE` | `gos-academy-assistant:local` |
| `APP_ENV_FILE` | `.env`; file được nạp vào các container ứng dụng |
| `KNOWLEDGE_MANIFEST` | `/app/data/samples/docker-manifest.json` |
| `LLM_PROVIDER` | `fake`; đổi sang `gemini` khi cần gọi provider thật |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | Bắt buộc khi dùng `gemini`; model phải khả dụng với tài khoản |
| `OMP_NUM_THREADS`, `EMBEDDING_BATCH_SIZE` | `2`, `16`; điều chỉnh theo tài nguyên CPU/RAM |
| `MAINTENANCE_INTERVAL_SECONDS` | `3600`, chu kỳ dọn quota hết hạn |
| `DATABASE_URL` | Chỉ cần tự cấu hình khi chạy Python trên host |

Trong Docker, kết nối được tạo từ `POSTGRES_*`, luôn dùng `db:5432` và escape mật khẩu trong URL. Compose ghi đè `POSTGRES_HOST`, cổng nội bộ, cache model (`/opt/models`) và thư mục FAQ (`/app/data/generated/teacher-faq`); `DATABASE_URL` dùng cho host được bỏ qua.

Các nhóm `EVENT_*`, `DELIVERY_*`, `TEACHER_REPLY_*`, `CONTEXT_*`, `WEBHOOK_*`, `CHUNK_*` và `RETRIEVAL_*` có mô tả/default trong file mẫu. Giá trị chứa `$` nên đặt trong dấu nháy đơn của `.env` để tránh Compose nội suy.

Để bật Gemini, sửa trực tiếp các dòng sau trong `.env`, giữ mỗi biến một lần:

```dotenv
LLM_PROVIDER=gemini
GEMINI_API_KEY=THAY_BANG_API_KEY_CUA_BAN
GEMINI_MODEL=THAY_BANG_MODEL_KHA_DUNG
```

Sau khi đổi cấu hình:

```console
docker compose up -d --wait --wait-timeout 180
```

`docker compose restart` không nạp lại biến môi trường của container. Để chạy kiểm tra demo offline, chuyển lại `LLM_PROVIDER=fake` và chạy `up -d`.

<a id="hoc-lieu"></a>
## Quản lý học liệu

### Học liệu đi kèm

Image chứa các fixture trong `data/samples/` và tự nhập tài liệu còn thiếu theo [docker-manifest.json](data/samples/docker-manifest.json). Đây là bộ trích đoạn DOCX/XLSX giả lập; không bao gồm toàn bộ tài liệu gốc trong `data/raw/`. Bản nháp so sánh RAG–Fine-tuning vẫn là `pending` và không được dùng làm bằng chứng trả lời.

Chỉ nội dung đủ điều kiện ở trạng thái `provided_sample` hoặc `approved_demo` được lập chỉ mục; riêng `approved_demo` bắt buộc có quyết định duyệt hợp lệ. `provided_sample` thể hiện dữ liệu mẫu được cung cấp, không phải phê duyệt giáo trình/chính sách thật. PDF scan cần OCR chưa được hỗ trợ.

### Nhập bộ tài liệu gốc

Nhận riêng bộ học liệu được bàn giao và đặt các file vào `data/raw/`. Script inventory hiện hỗ trợ ba DOCX gốc:

```text
01_Giao_trinh_Google_Sheets_KPI_Dashboard.docx
02_Giao_trinh_AI_Automation_n8n.docx
03_FAQ_Quy_dinh_lop_hoc_GIA_LAP.docx
```

Nếu đã có `manifest.local.json` được kiểm tra checksum/phạm vi lớp, dùng trực tiếp. Nếu chưa có, sau khi build image, chạy một container quản trị với mount cho phép ghi manifest. Ví dụ PowerShell dưới đây chỉ cấp quyền ghi cho lần chạy CLI này; mount runtime vẫn chỉ đọc:

```powershell
docker compose run --rm --no-deps --user 0:0 `
    --volume "${PWD}/data/raw:/source-review:rw" api `
    python -m scripts.inspect_sources --root /source-review --write-manifest /source-review/manifest.local.json
```

Kiểm tra manifest trước khi nhập; script không ghi đè manifest đã có. Các quy tắc inventory chỉ dành cho bộ tài liệu mẫu nêu trên. Học liệu khác cần manifest riêng theo [hợp đồng ingestion](docs/architecture/ingestion_contract.md).

Nếu muốn bootstrap dùng bộ gốc, đặt `KNOWLEDGE_MANIFEST=/app/data/raw/manifest.local.json` trong `.env` trước lần chạy đầu. Với tài liệu đã tồn tại, yêu cầu nhập/cập nhật rõ ràng qua CLI:

```console
docker compose exec api python -m scripts.ingest_knowledge inspect --manifest /app/data/raw/manifest.local.json
docker compose exec api python -m scripts.ingest_knowledge ingest --manifest /app/data/raw/manifest.local.json
docker compose exec api python -m scripts.ingest_knowledge status
```

Nhập lại đầu vào không đổi sẽ được bỏ qua. Khi sửa tài liệu, kiểm tra nội dung, cập nhật SHA-256 và metadata/version trong manifest. Không sửa học liệu chỉ để vượt qua parser hoặc tự nâng trạng thái duyệt. `--force` dùng để xây lại đầu vào hợp lệ không đổi; nguồn đã thu hồi không được tự kích hoạt lại.

Tra cứu chẩn đoán và thu hồi nguồn là thao tác quản trị:

```console
docker compose exec api python -m scripts.hybrid_search --group mock-ai-automation --query "Lỗi HTTP 401 trong n8n"
# Chỉ chạy khi thực sự cần thu hồi nguồn tương ứng:
# docker compose exec api python -m scripts.ingest_knowledge revoke --doc-id DOC-AUTO-01 --reason "Operator withdrawal"
```

<a id="giang-vien"></a>
## Giảng viên và quy trình FAQ

Credential nhân sự gắn với từng actor, vai trò và lớp; không sử dụng `OPERATOR_API_TOKEN` để thay thế. Ví dụ cấp quyền cho ba người tham gia riêng biệt:

```console
docker compose exec api python -m scripts.seed_teacher_demo --alias teacher-a --class-code AUTO-N8N-01 --role TEACHER
docker compose exec api python -m scripts.seed_teacher_demo --alias reviewer-b --class-code AUTO-N8N-01 --role FAQ_REVIEWER
docker compose exec api python -m scripts.seed_teacher_demo --alias operator-c --class-code AUTO-N8N-01 --role OPERATOR
```

Mỗi lệnh ghi credential vào file riêng trong `/app/.local/staff-credentials/` và chỉ in đường dẫn. Sao chép file được cấp ra thư mục riêng tư nếu cần nhập token vào giao diện; thay tên file bằng đường dẫn thực tế do lệnh trả về:

```powershell
New-Item -ItemType Directory -Force .local | Out-Null
docker compose cp api:/app/.local/staff-credentials/TEN_FILE_DUOC_CAP.token .local/
```

| Credential / vai trò | Mục đích |
| --- | --- |
| `MOCK_WEBHOOK_TOKEN` | Gửi webhook qua `X-Webhook-Token` |
| `OPERATOR_API_TOKEN` | Xem metrics và inbox mock qua `X-Operator-Token` |
| Staff `TEACHER` | Nhận ticket, soạn và duyệt phản hồi của mình trong lớp được cấp quyền |
| Staff `FAQ_REVIEWER` | Kiểm duyệt FAQ của actor khác trong lớp được cấp quyền |
| Staff `OPERATOR` | Xuất bản/thu hồi FAQ theo quyền lớp |

API nhân sự dùng `X-Teacher-Token`. Trong giao diện, quy trình gồm nhận ticket → lưu bản nháp → duyệt đúng revision/hash → worker gửi → ticket được giải quyết. FAQ tiếp tục qua đề xuất → gửi duyệt → reviewer độc lập duyệt → operator xuất bản. Cấp credential không thực hiện duyệt nội dung; duyệt phản hồi không đồng nghĩa duyệt FAQ.

Xem [runbook giảng viên/FAQ](docs/testing/teacher-faq-acceptance.md) để thao tác API, xử lý xung đột phiên bản, thu hồi credential và khôi phục khi publication lỗi.

<a id="van-hanh"></a>
## Vận hành và dữ liệu

```console
# Trạng thái và log gần nhất
docker compose ps -a
docker compose logs --tail 100 bootstrap api event-worker delivery-worker teacher-reply-worker maintenance

# Dừng và tạo lại container, giữ dữ liệu
docker compose down
docker compose up -d --wait --wait-timeout 180

# Sau khi cập nhật mã nguồn: kiểm tra thay đổi rồi build lại
docker compose up -d --build --wait --wait-timeout 180
```

| Dữ liệu | Nơi lưu trong Docker | Cần sao lưu khi chuyển instance |
| --- | --- | --- |
| Database, vector, sự kiện, ticket, outbox, audit | Volume `postgres_data` | Có |
| Artifact/manifest FAQ đã xuất bản | Volume `generated_data` | Có |
| File credential nhân sự đã cấp | Volume `staff_credentials` | Có, lưu riêng tư |
| Học liệu gốc | Bind mount `./data/raw`, chỉ đọc | Có, nếu đang sử dụng |
| Model E5 và mã ứng dụng | Trong application image | Qua gói image hoặc build lại |
| Cấu hình và secret | `.env` trên host | Quản lý riêng với bộ image |

Tên volume thực tế có tiền tố Compose project. Giữ ổn định thư mục/project name hoặc truyền cùng `-p TEN_PROJECT` để tiếp tục dùng đúng volume. Đổi tên project có thể tạo một instance dữ liệu mới.

`docker compose down` giữ volume; **`docker compose down -v` xóa dữ liệu của các named volume**. Restart policy hoạt động khi Docker daemon đang chạy. Log container có giới hạn xoay vòng 10 MB × 3 file cho mỗi service.

Khi sao lưu instance đang sử dụng, cần giữ cả PostgreSQL lẫn artifact/credential tương ứng. Có thể tạo database dump bằng `pg_dump` trong service `db`, rồi dùng `docker compose cp` để lấy file; sao lưu thêm hai volume ứng dụng và học liệu gốc. Thử phục hồi vào một instance riêng trước khi nâng cấp hoặc thay máy. Gói image ở phần tiếp theo chỉ tạo môi trường chạy mới, không mang theo dữ liệu nghiệp vụ.

<a id="ban-giao"></a>
## Bàn giao sang máy khác

### Máy nhận có Internet

Clone đúng nhánh/tag được bàn giao, tạo `.env` riêng và làm theo [hướng dẫn Docker](#cai-dat). Lần build đầu sẽ tải dependency/model. Cần chuyển thêm bộ học liệu gốc nếu muốn dùng corpus đầy đủ.

### Bàn giao bằng image, không build trên máy nhận

Sau khi build và chạy thành công ở máy nguồn:

```powershell
.\scripts\package-docker.ps1
```

Script tạo `.local/docker-handover/` gồm `images.tar`, checksum SHA-256, `compose.yaml`, `.env.example`, script tạo cấu hình và `START-HERE.txt`. Thư mục này được Git bỏ qua; phải chuyển riêng cho máy nhận. Nếu thư mục đầu ra đã có, chọn tên mới bằng `-OutputDirectory .local/docker-handover-v2`.

Chép **toàn bộ thư mục** sang máy dùng cùng kiến trúc CPU, cài/bật Docker rồi mở PowerShell tại thư mục gói:

```powershell
# Đối chiếu với giá trị trong images.tar.sha256 trước khi nạp.
Get-FileHash .\images.tar -Algorithm SHA256

docker image load -i images.tar
.\scripts\setup-docker.ps1
docker compose up -d --no-build --pull never --wait --wait-timeout 180
docker compose exec api python -m scripts.smoke_docker
```

Chế độ `fake` chạy với model E5 và fixture đã có trong image, không cần mạng tới provider. Gemini vẫn cần Internet và API key. Gói không chứa `.env`, database hiện có, credential đã cấp hoặc tài liệu gốc; khi chuyển một instance đang hoạt động, sao lưu/phục hồi các dữ liệu này riêng.

<a id="phat-trien"></a>
## Phát triển bằng Python trên máy host

Dùng cách này khi cần debug/reload mã Python. Cần Python 3.11 và Docker cho PostgreSQL. Không chạy đồng thời API/worker Docker và API/worker host trên cùng cổng hoặc database nếu không chủ đích chia sẻ công việc.

Tạo `.env` như ở bước cài đặt, để `POSTGRES_HOST` trống. Điền `DATABASE_URL` trỏ tới `localhost`, đúng `POSTGRES_PORT`, database, user và mật khẩu. Mật khẩu trong URL phải được URL-encode. Các lệnh dưới đây dành cho PowerShell:

```powershell
docker compose up -d --wait db
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --index-url https://download.pytorch.org/whl/cpu "torch==2.7.1+cpu"
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m scripts.seed
.\.venv\Scripts\python.exe -m app.knowledge.embeddings --download
.\.venv\Scripts\python.exe -m scripts.ingest_knowledge ingest --manifest data/samples/docker-manifest.json
```

Chạy **mỗi lệnh sau trong một terminal riêng**, đều tại thư mục gốc repository:

```powershell
# Terminal 1: API và giao diện demo
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# Terminal 2: xử lý sự kiện
.\.venv\Scripts\python.exe -m scripts.run_event_worker --poll-interval 1

# Terminal 3: giao tin mock
.\.venv\Scripts\python.exe -m scripts.run_delivery_worker --poll-interval 1

# Terminal 4: phản hồi giảng viên
.\.venv\Scripts\python.exe -m scripts.run_teacher_reply_worker --poll-interval 1

# Terminal 5: dọn quota hết hạn theo chu kỳ
.\.venv\Scripts\python.exe -m scripts.run_maintenance_worker
```

Cache model trên host mặc định là `.models/`, khác cache bên trong image. `requirements-docker.lock` dành cho runtime Linux; môi trường phát triển host dùng `requirements.txt`.

<a id="kiem-thu"></a>
## Kiểm thử

Trong môi trường Python đã chuẩn bị:

```powershell
.\.venv\Scripts\python.exe -m scripts.check_sample_materials
.\.venv\Scripts\python.exe -m pytest -q
```

Khi chưa đặt `TEST_DATABASE_URL`, các bài integration PostgreSQL được đánh dấu skipped. Để chạy đầy đủ, tạo một database riêng có `test` hoặc `smoke` trong tên, đặt `TEST_DATABASE_URL`, migrate và seed **chính database test đó** trước khi chạy. Không dùng database đang phục vụ demo/người dùng.

```powershell
# Ví dụ tạo database test mới khi POSTGRES_USER=gos; bỏ qua nếu đã có.
docker compose exec db createdb -U gos gos_test

# Thay URL bằng kết nối tới database test riêng đã được tạo.
$env:TEST_DATABASE_URL = 'postgresql+psycopg://gos:MAT_KHAU_DA_URL_ENCODE@localhost:5432/gos_test'
$env:DATABASE_URL = $env:TEST_DATABASE_URL
try {
    .\.venv\Scripts\python.exe -m alembic upgrade head
    .\.venv\Scripts\python.exe -m scripts.seed
    .\.venv\Scripts\python.exe -m pytest -q
} finally {
    Remove-Item Env:DATABASE_URL
    Remove-Item Env:TEST_DATABASE_URL
}
```

Chạy các lệnh trên trong terminal test riêng; khi xóa biến môi trường, ứng dụng lại đọc cấu hình từ `.env`. Kiểm tra học liệu gốc, E5 live và Gemini live là opt-in, có thể cần dữ liệu riêng, Internet và chi phí provider. Xem các báo cáo acceptance bên dưới trước khi bật `ORIGINAL_SOURCES_MANIFEST`, `RUN_LIVE_EMBEDDING` hoặc `RUN_LIVE_GENERATION`.

Workflow [smoke.yml](.github/workflows/smoke.yml) định nghĩa hai nhóm kiểm tra: lint/migration/seed/Pytest với PostgreSQL thật; và build image có E5, chạy toàn bộ Compose, smoke hỏi–đáp rồi kiểm tra lại sau khi tạo lại container. Cấu hình CI không thay thế kết quả chạy CI của từng commit.

<a id="su-co"></a>
## Xử lý sự cố

| Triệu chứng | Cách kiểm tra / xử lý |
| --- | --- |
| Không kết nối được Docker daemon | Bật Docker Desktop/Engine, chọn Linux containers, kiểm tra `docker version` |
| Không có `Dockerfile` sau clone | Checkout nhánh/tag chứa bản Docker đã được bàn giao; nhánh chỉ có ở máy maintainer cần được công bố trước |
| Thiếu secret hoặc bootstrap thoát `1` | Đối chiếu `.env.example`, kiểm tra token khác nhau và đủ độ dài; xem `docker compose logs bootstrap` |
| Cổng 8000/5432 đã được sử dụng | Đổi `API_PORT`/`POSTGRES_PORT`, chạy lại `up -d`; kết nối trong Docker vẫn là `db:5432` |
| Database lỗi xác thực sau khi sửa `.env` | Dùng credential đúng của volume đã khởi tạo; thay mật khẩu trong PostgreSQL theo quy trình quản trị, không xóa volume để khắc phục |
| Webhook nhận nhưng không thấy phản hồi | Kiểm tra event/delivery worker, metrics, event status và inbox; ACK chưa phải kết quả xử lý |
| Token đúng nhưng API trả `401`/`403`/`404` | Kiểm tra đúng loại header, token còn hiệu lực, binding nhóm và quyền lớp của actor |
| Manifest/checksum không hợp lệ | Kiểm tra file gốc, đường dẫn trong manifest và SHA-256; không tự bỏ kiểm tra hoặc sửa quyết định duyệt |
| Không tìm thấy tokenizer/model | Docker: kiểm tra image chứa E5 và build lại nếu cần; host: chạy `python -m app.knowledge.embeddings --download` trong đúng venv/cache |
| Sửa `.env` nhưng cấu hình chưa đổi | Dùng `docker compose up -d`, không chỉ `restart`; kiểm tra cả `APP_ENV_FILE` nếu dùng file riêng |
| Sau đổi thư mục/project không thấy dữ liệu | Kiểm tra Compose project name và các volume đang gắn; tránh ghi dữ liệu mới trước khi xác định đúng instance |
| Gemini lỗi hoặc không có model | Kiểm tra API key, quyền truy cập model, quota và kết nối mạng; `fake` dùng được cho kiểm tra offline |

<a id="tai-lieu"></a>
## Cấu trúc mã nguồn và tài liệu

```text
app/
  api/          # Webhook, API nhân sự, health và giao diện demo
  knowledge/    # Parser, manifest, chunking, E5, vòng đời tài liệu
  retrieval/    # Scope lớp, vector/FTS và RRF
  generation/   # Evidence gate, chọn trích dẫn, validation
  context/      # Ngữ cảnh câu hỏi tiếp nối có giới hạn
  teaching/     # Kiểm tra mức bao phủ và trình bày câu trả lời
  llm/          # Fake và Gemini adapter
  events/       # Admission, hàng đợi, lease, retry và xử lý sự kiện
  delivery/     # Outbox và giao tin mock
  teacher/      # Actor, phân quyền, ticket và phản hồi giảng viên
  faq/          # Kiểm duyệt, export và xuất bản FAQ
  demo_admin/   # HTML/CSS/JavaScript của trang quản trị
alembic/        # Migration database
scripts/        # CLI quản trị, worker, setup và đóng gói Docker
tests/          # Unit, integration và acceptance fixtures
data/samples/   # Học liệu/sự kiện giả lập được đưa vào Git
docs/           # Hợp đồng kiến trúc và báo cáo kiểm thử
```

| Chủ đề | Kiến trúc / hợp đồng | Kiểm thử / hướng dẫn |
| --- | --- | --- |
| Dữ liệu mẫu và webhook | [Sample contracts](docs/architecture/sample_contracts.md) | [PostgreSQL smoke](docs/testing/smoke-postgresql17-2026-11-05.md) |
| Nhập học liệu và vòng đời nguồn | [Ingestion](docs/architecture/ingestion_contract.md) | [RAG ingestion acceptance](docs/testing/rag-ingestion-acceptance.md) |
| Tìm kiếm theo phạm vi lớp | [Hybrid retrieval](docs/architecture/hybrid_retrieval.md) | [Retrieval acceptance](docs/testing/hybrid-retrieval-acceptance.md) |
| Trả lời có bằng chứng | [Grounded generation](docs/architecture/grounded_generation.md), [Quote selection](docs/architecture/multi_claim_selection.md) | [Generation acceptance](docs/testing/grounded-generation-acceptance.md), [Multi-claim acceptance](docs/testing/multiclaim-acceptance.md) |
| Retry, outbox và giao tin | [Event reliability](docs/architecture/event_reliability.md) | [Reliability acceptance](docs/testing/event-reliability-acceptance.md) |
| Ngữ cảnh và cách trình bày | [Contextual teaching](docs/architecture/contextual_teaching_quality.md) | [Teaching acceptance](docs/testing/contextual-teaching-acceptance.md) |
| Giảng viên và FAQ | [Teacher/FAQ workflow](docs/architecture/teacher_faq_workflow.md) | [Teacher/FAQ runbook](docs/testing/teacher-faq-acceptance.md) |

Các điểm Recall/Hit trong tài liệu acceptance là kết quả trên tập đánh giá và phiên bản corpus được ghi rõ trong từng báo cáo; không phải cam kết chất lượng trên dữ liệu khác. Câu trả lời hiện tại dựa vào trích dẫn chính xác, chưa hỗ trợ diễn giải tự do như một giảng viên. Vận hành thật cần đánh giá học liệu, phân quyền, bảo mật, chất lượng giảng dạy và tích hợp kênh gửi trên môi trường triển khai tương ứng.
