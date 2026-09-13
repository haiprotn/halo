"""Sinh file `ads.js` cho trang chào (captive portal) trên router.

Portal chạy HOÀN TOÀN ĐỘC LẬP với VPS: file này được đẩy xuống router một lần,
sau đó VPS sập hay site mất mạng thì khách vẫn đăng nhập và vẫn thấy banner.
Đó là lý do toàn bộ logic chọn banner nằm trong chính file JS này, không gọi API.

★ Quy ước thứ trong tuần: dữ liệu lưu theo Python (0 = Thứ Hai … 6 = Chủ Nhật).
  JavaScript thì `getDay()` trả 0 = Chủ Nhật. Phép đổi `(getDay() + 6) % 7` nằm
  ngay trong file sinh ra — sai chỗ này là banner chạy lệch đúng một ngày và rất
  khó phát hiện.

★ Bẫy đã gặp: `src=""` trên thẻ <img> kích hoạt sự kiện lỗi ngay lúc tải trang,
  khối quảng cáo bị ẩn trước khi script kịp gán ảnh. Vì vậy hàm `showAd` dưới đây
  gán `onerror` TRƯỚC rồi mới gán `src`, và không bao giờ để `src` rỗng.
"""

from __future__ import annotations

import json

from ..models import Banner

ADS_FILENAME = "ads.js"


def banner_to_dict(b: Banner) -> dict:
    return {
        "file": b.filename,
        "days": [int(d) for d in b.days.split(",") if d.strip().isdigit()],
        "from": b.start_time,
        "to": b.end_time,
        "slots": [s.strip() for s in b.slots.split(",") if s.strip()],
        "link": b.link_url or "",
    }


def render(banners: list[Banner], site_name: str = "") -> str:
    """Trả về nội dung file ads.js dạng chuỗi."""
    data = [banner_to_dict(b) for b in banners if b.active]
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    return f"""/* ads.js — sinh tự động bởi cổng quản trị. ĐỪNG SỬA TAY TRÊN ROUTER.
   Địa điểm: {site_name or '(không tên)'}
   Sửa banner ở cổng quản trị rồi bấm "Đẩy xuống router"; lần đẩy sau sẽ ghi đè file này. */
(function (global) {{
  "use strict";

  var ADS = {payload};

  function toMinutes(hhmm) {{
    var p = String(hhmm || "00:00").split(":");
    return (parseInt(p[0], 10) || 0) * 60 + (parseInt(p[1], 10) || 0);
  }}

  /* Chọn một banner hợp lệ cho khung giờ hiện tại và đúng trang đang hiển thị.
     slot: "login" (trang chào) | "alogin" (trang sau khi đăng nhập) | "status" */
  function pickAd(slot, now) {{
    now = now || new Date();
    var day = (now.getDay() + 6) % 7;            /* đổi Chủ Nhật=0 sang Thứ Hai=0 */
    var minute = now.getHours() * 60 + now.getMinutes();
    var candidates = [];

    for (var i = 0; i < ADS.length; i++) {{
      var ad = ADS[i];
      if (ad.days.indexOf(day) === -1) continue;
      if (ad.slots.indexOf(slot) === -1) continue;
      var from = toMinutes(ad.from), to = toMinutes(ad.to);
      if (from <= to) {{
        if (minute < from || minute > to) continue;
      }} else {{                                  /* khung giờ vắt qua nửa đêm */
        if (minute < from && minute > to) continue;
      }}
      candidates.push(ad);
    }}
    if (!candidates.length) return null;
    return candidates[Math.floor(Math.random() * candidates.length)];
  }}

  /* Gắn banner vào một thẻ <img>. Gán onerror TRƯỚC rồi mới gán src. */
  function showAd(imgId, slot, linkId) {{
    var img = document.getElementById(imgId);
    if (!img) return null;
    var ad = pickAd(slot);
    if (!ad) {{ img.style.display = "none"; return null; }}

    img.onerror = function () {{ img.style.display = "none"; }};
    img.onload = function () {{ img.style.display = "block"; }};
    img.src = ad.file;

    if (linkId && ad.link) {{
      var a = document.getElementById(linkId);
      if (a) {{ a.href = ad.link; a.target = "_blank"; a.rel = "noopener"; }}
    }}
    return ad;
  }}

  global.ADS = ADS;
  global.pickAd = pickAd;
  global.showAd = showAd;
}})(window);
"""
