import 'dart:convert';
import 'dart:io';

import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';

/// 錄下長者說的一句話，交給 [ApiClient.chat] 的 audio 參數送後端辨識（STT）。
///
/// 對應 docs/api.md 的 `/chat` audio 路線：客語（`hak`）裝置端無法辨識，改錄音送後端；
/// 華語也可走此路線讓後端統一辨識。錄成 m4a（AAC-LC）——api.md 的 `audio.format` 收
/// `m4a`｜`wav`，單句上限 60 秒（超過後端回 400 `AUDIO_TOO_LONG`，UI 應在到點前收音）。
class AudioRecorderService {
  final AudioRecorder _recorder = AudioRecorder();

  /// 是否已取得麥克風權限；首次呼叫會觸發系統權限請求。
  Future<bool> hasPermission() => _recorder.hasPermission();

  Future<bool> get isRecording => _recorder.isRecording();

  /// 開始錄音；沒有麥克風權限時回傳 false、不錄。
  Future<bool> start() async {
    if (!await _recorder.hasPermission()) return false;
    final dir = await getTemporaryDirectory();
    final path =
        '${dir.path}/chat_${DateTime.now().millisecondsSinceEpoch}.m4a';
    await _recorder.start(
      const RecordConfig(encoder: AudioEncoder.aacLc),
      path: path,
    );
    return true;
  }

  /// 停止錄音，回傳 base64 音檔（可直接餵給 `chat(audioBase64: ...)`）；沒錄到內容回 null。
  ///
  /// 讀完即刪本地暫存音檔——語音屬個資（見 docs/pii.md），不留在裝置上。
  Future<String?> stop() async {
    final path = await _recorder.stop();
    if (path == null) return null;
    final file = File(path);
    if (!await file.exists()) return null;
    final bytes = await file.readAsBytes();
    await file.delete();
    return bytes.isEmpty ? null : base64Encode(bytes);
  }

  /// 取消錄音並丟棄音檔（使用者中途放棄時用）。
  Future<void> cancel() => _recorder.cancel();

  void dispose() => _recorder.dispose();
}
