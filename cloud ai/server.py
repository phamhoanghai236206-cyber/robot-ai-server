import os
import datetime
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse, FileResponse
from google import genai
from google.genai import types
import edge_tts

app = FastAPI(title="Robot AI Middle Server")

# 1. Khởi tạo kết nối AI Cloud (Gemini)
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

# Tạo thư mục lưu trữ bài giảng & phản hồi âm thanh
os.makedirs("luu_tru_bai_giang", exist_ok=True)
os.makedirs("temp_audio", exist_ok=True)

# Đọc dữ liệu từ điển tiếng dân tộc (nếu có)
def load_ethnic_dictionary():
    if os.path.exists("tu_dien_dan_toc.txt"):
        with open("tu_dien_dan_toc.txt", "r", encoding="utf-8") as f:
            return f.read()
    return "Chưa có từ điển bổ sung."

# 2. Cấu hình câu lệnh (Prompt) cho từng tính năng của Robot
PROMPTS = {
    # Mục 1 & 8: Giao tiếp song ngữ & Đánh giá độ lịch sự ngoại giao
    "english_diplomacy": (
        "Bạn là trợ lý ngoại giao và gia sư tiếng Anh song ngữ. "
        "Hãy nghe câu nói của người dùng và trả lời ngắn gọn (tối đa 4 câu để đọc qua loa): "
        "1) Chấm mức độ lịch sự/trang trọng trong môi trường ngoại giao. "
        "2) Gợi ý cách diễn đạt chuẩn xác, lịch sự hơn bằng tiếng Anh và giải thích ngắn bằng tiếng Việt."
    ),
    # Mục 2 & 11: Tin tức quốc tế & Phân tích hình ảnh Việt Nam trên báo nước ngoài
    "foreign_press": (
        "Bạn là chuyên gia phân tích truyền thông quốc tế. Hãy dùng công cụ tìm kiếm để tra cứu "
        "các bài báo quốc tế mới nhất liên quan đến câu hỏi. Nếu người dùng hỏi về hình ảnh Việt Nam, "
        "hãy thống kê ngắn gọn: các lĩnh vực được nhắc đến nhiều nhất, chủ đề nổi bật và sắc thái (tích cực/trung lập/tiêu cực). "
        "Trình bày súc tích dưới 100 từ để robot đọc thành tiếng."
    ),
    # Mục 14: Phiên dịch tiếng Sán Dìu, Cao Lan, Mông <-> Việt <-> Anh
    "ethnic_translate": (
        "Bạn là phiên dịch viên ngôn ngữ dân tộc (Sán Dìu, Cao Lan, Mông) sang tiếng Việt và tiếng Anh. "
        "Hãy ưu tiên tra cứu kho từ vựng chuẩn dưới đây trước khi dịch:\n"
        f"{load_ethnic_dictionary()}\n"
        "Trả về bản dịch rõ ràng giữa các ngôn ngữ theo yêu cầu của người nói."
    ),
    # Mục 13 & 1(cuối): Thu âm bài giảng của giáo viên, số hóa và dịch sang ngôn ngữ khác
    "lecture_archive": (
        "Hãy chép lại chính xác toàn bộ nội dung bài giảng trong file âm thanh (Transcript), "
        "sau đó dịch sang tiếng Anh (hoặc ngôn ngữ được yêu cầu). Trình bày rõ ràng 2 phần: [Bản gốc] và [Bản dịch]."
    )
}

# Hàm chuyển văn bản thành file âm thanh MP3 gửi về ESP 1
async def text_to_speech(text: str, output_path: str):
    # Giọng nữ tiếng Việt chuẩn (tự động đọc tốt cả từ tiếng Anh xen kẽ)
    communicate = edge_tts.Communicate(text, voice="vi-VN-HoaiMyNeural")
    await communicate.save(output_path)

# API 1: Nhận giọng nói từ ESP 1
@app.post("/api/voice")
async def process_voice(
    audio: UploadFile = File(...),
    mode: str = Form("english_diplomacy"),
    session_id: str = Form("default")
):
    audio_bytes = await audio.read()
    system_prompt = PROMPTS.get(mode, PROMPTS["english_diplomacy"])

    # Nếu là chế độ phân tích báo chí quốc tế -> Bật Google Search cho AI
    tools = [types.Tool(google_search=types.GoogleSearch())] if mode == "foreign_press" else None

    # Gửi trực tiếp file ghi âm lên Gemini Cloud
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            types.Part.from_bytes(data=audio_bytes, mime_type="audio/wav"),
            "Hãy lắng nghe và thực hiện đúng nhiệm vụ được giao."
        ],
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=tools,
            temperature=0.3
        )
    )
    reply_text = response.text

    # Nếu là chế độ thu âm bài giảng -> Lưu file gốc + văn bản để bảo tồn
    if mode == "lecture_archive":
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        with open(f"luu_tru_bai_giang/bai_giang_{timestamp}.wav", "wb") as f:
            f.write(audio_bytes)
        with open(f"luu_tru_bai_giang/bai_giang_{timestamp}.txt", "w", encoding="utf-8") as f:
            f.write(reply_text)

    # Tạo file âm thanh phản hồi cho ESP 1
    out_audio = f"temp_audio/reply_{session_id}.mp3"
    await text_to_speech(reply_text, out_audio)

    return {
        "status": "ready", # Báo hiệu ESP 1 bật ĐÈN XANH
        "session_id": session_id,
        "text": reply_text,
        "audio_url": f"/api/audio/{session_id}"
    }

# API 2: Nhận ảnh chụp tài liệu/báo in từ ESP 3 (OV5640)
@app.post("/api/read-doc")
async def process_document(
    image: UploadFile = File(...),
    session_id: str = Form("default")
):
    img_bytes = await image.read()
    prompt = (
        "Đọc văn bản tiếng Anh trong ảnh chụp trang báo/tài liệu này. "
        "Hãy tóm tắt nội dung chính bằng tiếng Việt thật dễ hiểu (khoảng 3-4 câu) "
        "và giải thích 2 thuật ngữ tiếng Anh quan trọng nhất xuất hiện trong bài."
    )

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"),
            prompt
        ]
    )
    reply_text = response.text

    out_audio = f"temp_audio/reply_{session_id}.mp3"
    await text_to_speech(reply_text, out_audio)

    return {
        "status": "ready",
        "session_id": session_id,
        "text": reply_text,
        "audio_url": f"/api/audio/{session_id}"
    }

# API 3: ESP 1 tải file âm thanh về phát ra 2 loa MAX98357A
@app.get("/api/audio/{session_id}")
async def get_audio(session_id: str):
    return FileResponse(f"temp_audio/reply_{session_id}.mp3", media_type="audio/mpeg")