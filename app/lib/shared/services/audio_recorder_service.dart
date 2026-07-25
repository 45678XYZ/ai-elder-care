import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';

/// 錄下長者說的一句話，交給 [ApiClient.chat] 的 audio 參數送後端辨識（STT）。
///
/// 對應 docs/api.md 的 `/chat` audio 路線：客語（`hak`）裝置端無法辨識，改錄音送後端；
/// 華語也可走此路線讓後端統一辨識。錄成 m4a（AAC-LC）。
///
/// 單句上限 60 秒（[maxDuration]）：超過後端回 400 `AUDIO_TOO_LONG`，所以這裡到點就
/// 自行收音，不讓長者講完一大段才被退。
class AudioRecorderService {
  final AudioRecorder _recorder = AudioRecorder();

  /// 單句音檔上限，對齊 docs/api.md 的 `audio.data`。
  static const Duration maxDuration = Duration(seconds: 60);

  Timer? _limitTimer;
  DateTime? _startedAt;

  /// 到點自動收音時先收著的結果，等呼叫端的 [stop] 來領。
  String? _autoStopped;

  /// 是否已取得麥克風權限；首次呼叫會觸發系統權限請求。
  Future<bool> hasPermission() => _recorder.hasPermission();

  Future<bool> get isRecording => _recorder.isRecording();

  /// 已錄多久；沒在錄時為零。UI 可據此顯示剩餘秒數。
  Duration get elapsed => _startedAt == null
      ? Duration.zero
      : DateTime.now().difference(_startedAt!);

  /// 距離上限還剩多久；已到點為零。
  Duration get remaining {
    final left = maxDuration - elapsed;
    return left.isNegative ? Duration.zero : left;
  }

  /// 開始錄音；沒有麥克風權限時回傳 false、不錄。
  ///
  /// 錄滿 [maxDuration] 會自動收音並呼叫 [onLimitReached]，讓 UI 收掉聆聽狀態。
  /// 收到通知後照常 `await stop()` 即可拿到音檔——自動收的那份會在那裡回給你，
  /// 呼叫端不必分兩條路徑處理。
  Future<bool> start({void Function()? onLimitReached}) async {
    if (!await _recorder.hasPermission()) return false;
    final dir = await getTemporaryDirectory();
    final path =
        '${dir.path}/chat_${DateTime.now().millisecondsSinceEpoch}.m4a';
    await _recorder.start(
      const RecordConfig(encoder: AudioEncoder.aacLc),
      path: path,
    );

    _autoStopped = null;
    _startedAt = DateTime.now();
    _limitTimer?.cancel();
    _limitTimer = Timer(maxDuration, () async {
      _autoStopped = await _readAndDelete(await _recorder.stop());
      _startedAt = null;
      onLimitReached?.call();
    });
    return true;
  }

  /// 停止錄音，回傳 base64 音檔（可直接餵給 `chat(audioBase64: ...)`）；沒錄到內容回 null。
  Future<String?> stop() async {
    _limitTimer?.cancel();
    _limitTimer = null;
    _startedAt = null;

    // 已達上限自動收過音了，把那份交出去（重複呼叫 recorder.stop() 只會拿到 null）。
    final auto = _autoStopped;
    if (auto != null) {
      _autoStopped = null;
      return auto;
    }
    return _readAndDelete(await _recorder.stop());
  }

  /// 取消錄音並丟棄音檔（使用者中途放棄時用）。
  Future<void> cancel() async {
    _limitTimer?.cancel();
    _limitTimer = null;
    _startedAt = null;
    _autoStopped = null;
    await _recorder.cancel();
  }

  /// 讀出音檔轉 base64，讀完即刪本地暫存——語音屬個資（見 docs/pii.md），不留在裝置上。
  Future<String?> _readAndDelete(String? path) async {
    if (path == null) return null;
    final file = File(path);
    if (!await file.exists()) return null;
    final bytes = await file.readAsBytes();
    await file.delete();
    return bytes.isEmpty ? null : base64Encode(bytes);
  }

  void dispose() {
    _limitTimer?.cancel();
    _recorder.dispose();
  }
}
