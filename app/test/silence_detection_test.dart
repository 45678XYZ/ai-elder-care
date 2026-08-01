import 'package:e_hakka_care/shared/services/audio_recorder_service.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:record/record.dart';

/// 錄音路徑的斷句行為（客語走這條）。
///
/// 華語的斷句是 `speech_to_text` 的 `pauseFor` 內建給的，錄音這條沒有人給，只有
/// 60 秒上限——靠它收音的話長輩講完一句要乾等將近一分鐘，免手持迴圈等於壞掉。
/// 所以這段是自己做的，也因此更需要測：它決定「長輩講完話多久會被送出去」，
/// 調錯了不會報錯，只會讓對話變得很難用。
void main() {
  /// 每 200ms 讀一次音量，所以推進 N 毫秒 ≈ N/200 次取樣。
  const poll = Duration(milliseconds: 200);

  /// 掛一個錄音服務並開始錄音；[levels] 是每次取樣要回的音量（dBFS）。
  ///
  /// 用 testWidgets 而不是 test：計時器要在 FakeAsync 下才推得動，`tester.pump`
  /// 是最直接的推進方式。這裡沒有任何 widget。
  Future<(AudioRecorderService, List<AudioRecorderStopReason>)> startWith(
    WidgetTester tester,
    List<double> levels,
  ) async {
    final recorder = _FakeRecorder(levels);
    final service = AudioRecorderService(
      recorder: recorder,
      tempDirPath: () async => '/tmp',
    );
    final stops = <AudioRecorderStopReason>[];
    await service.start(onDone: stops.add);
    await tester.pump();
    return (service, stops);
  }

  /// 推進 [ticks] 次取樣。
  Future<void> advance(WidgetTester tester, int ticks) async {
    for (var i = 0; i < ticks; i++) {
      await tester.pump(poll);
    }
  }

  const speaking = -10.0; // 明顯高於門檻
  const silent = -60.0; // 明顯低於門檻

  testWidgets('講完話靜下來三秒才送出', (tester) async {
    // 講 1 秒（5 次）→ 靜下來
    final (service, stops) = await startWith(
      tester,
      [...List.filled(5, speaking), ...List.filled(60, silent)],
    );
    addTearDown(service.dispose);

    // 靜音 2 秒還不能停——長輩句中停頓本來就有這麼長。
    await advance(tester, 5 + 10);
    expect(stops, isEmpty, reason: '靜音 2 秒就切斷會把長輩的話從中間砍掉');

    // 滿三秒才停。
    await advance(tester, 5);
    expect(stops, [AudioRecorderStopReason.silence]);
  });

  testWidgets('句中停頓不算講完，接著講就重新計時', (tester) async {
    // 講 1 秒 → 停頓 2 秒（想詞）→ 又講 1 秒 → 才真的靜下來
    final (service, stops) = await startWith(tester, [
      ...List.filled(5, speaking),
      ...List.filled(10, silent),
      ...List.filled(5, speaking),
      ...List.filled(60, silent),
    ]);
    addTearDown(service.dispose);

    // 走完「講→停頓→再講」，中途不該停。
    await advance(tester, 20);
    expect(stops, isEmpty, reason: '停頓後又開口，那一次停頓不能算講完');

    // 這次是真的講完了，再等三秒。
    await advance(tester, 15);
    expect(stops, [AudioRecorderStopReason.silence]);
  });

  testWidgets('還沒開口的那段安靜不算靜音', (tester) async {
    // 長輩點了麥克風還在想要說什麼，開頭本來就是安靜的。
    final (service, stops) = await startWith(tester, [
      ...List.filled(20, silent), // 4 秒沒講話
      ...List.filled(5, speaking),
      ...List.filled(60, silent),
    ]);
    addTearDown(service.dispose);

    // 開頭安靜 4 秒（已超過三秒門檻）不該送出一段空白音檔。
    await advance(tester, 20);
    expect(stops, isEmpty, reason: '還沒開口就送出，等於送一段空白給後端辨識');

    await advance(tester, 5 + 15);
    expect(stops, [AudioRecorderStopReason.silence]);
  });

  testWidgets('一直沒開口八秒後收掉，而且分得出是這個原因', (tester) async {
    final (service, stops) = await startWith(tester, List.filled(60, silent));
    addTearDown(service.dispose);

    await advance(tester, 35); // 7 秒
    expect(stops, isEmpty);

    await advance(tester, 10); // 超過 8 秒
    // 原因要分得出來：沒聽到聲音時畫面要提示長輩再說一次，講完了則不必多說。
    expect(stops, [AudioRecorderStopReason.noSpeech]);
  });

  testWidgets('停了就不會再停第二次', (tester) async {
    final (service, stops) = await startWith(
      tester,
      [...List.filled(5, speaking), ...List.filled(200, silent)],
    );
    addTearDown(service.dispose);

    await advance(tester, 60);
    // 收音是 async 的，這段期間輪詢可能又被觸發——重複 stop 會把音檔蓋成 null。
    expect(stops.length, 1);
  });
}

/// 照腳本回音量的假錄音器。用完腳本就一直回最後一個值。
class _FakeRecorder implements AudioRecorder {
  _FakeRecorder(this._levels);

  final List<double> _levels;
  int _i = 0;

  @override
  Future<Amplitude> getAmplitude() async {
    final v = _levels[_i < _levels.length ? _i : _levels.length - 1];
    _i++;
    return Amplitude(current: v, max: v);
  }

  @override
  Future<bool> hasPermission({bool request = true}) async => true;

  @override
  Future<void> start(RecordConfig config, {required String path}) async {}

  /// 回 null：測試只驗「什麼時候停、為什麼停」，不驗檔案讀寫。
  /// 回真的路徑會讓服務去讀檔，那是 FakeAsync 推不動的真實 IO。
  @override
  Future<String?> stop() async => null;

  @override
  Future<void> cancel() async {}

  @override
  Future<void> dispose() async {}

  @override
  Future<bool> isRecording() async => true;

  @override
  noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}
