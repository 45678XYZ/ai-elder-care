import 'dart:async';

import 'package:e_hakka_care/shared/widgets/async_view.dart';
import 'package:e_hakka_care/shared/widgets/auto_refresh.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

/// 畫面自己保持新鮮這件事的規則。
///
/// 為什麼要釘：照護者那四頁原本是「initState 載一次就再也不動」，開著摘要頁等長輩
/// 講話，等到的永遠是打開那一刻的那份。修好之後最容易再壞掉的是**代價**那一側——
/// 四頁同時在背景輪詢、切回分頁連打好幾次、背景重拉把捲動位置彈回最上面。
/// 這裡把「該拉」與「不該拉」兩邊一起釘住。
void main() {
  group('AutoRefreshState', () {
    testWidgets('看得到的時候，每過一輪就重拉一次', (tester) async {
      final host = _HostKey();
      await tester.pumpWidget(_wrap(_Host(key: host)));
      expect(host.currentState!.count, 0,
          reason: '掛上去當下不該多拉一次——initState 已經載過了');

      await tester.pump(const Duration(seconds: 61));
      expect(host.currentState!.count, 1);

      await tester.pump(const Duration(seconds: 61));
      expect(host.currentState!.count, 2);
    });

    testWidgets('看不到的分頁不拉', (tester) async {
      // IndexedStack 會把所有分頁都建起來（只是不畫），四頁各開一個計時器就是
      // 每分鐘四次請求、其中三次沒有人在看。go_router 給背景分頁包的正是
      // TickerMode(enabled: false)。
      final host = _HostKey();
      await tester.pumpWidget(_wrap(_Host(key: host), visible: false));

      await tester.pump(const Duration(seconds: 61));
      await tester.pump(const Duration(seconds: 61));
      expect(host.currentState!.count, 0);
    });

    testWidgets('切回這個分頁的當下就拉一次，不用等下一輪', (tester) async {
      final host = _HostKey();
      await tester.pumpWidget(_wrap(_Host(key: host), visible: false));
      await tester.pump(const Duration(seconds: 61));
      expect(host.currentState!.count, 0);

      await tester.pumpWidget(_wrap(_Host(key: host), visible: true));
      await tester.pump();
      expect(host.currentState!.count, 1, reason: '照護者切過去看一眼通常只停留幾秒，等不到下一輪');
    });

    testWidgets('快速來回切分頁不會連打', (tester) async {
      final host = _HostKey();
      await tester.pumpWidget(_wrap(_Host(key: host), visible: false));
      for (var i = 0; i < 4; i++) {
        await tester.pumpWidget(_wrap(_Host(key: host), visible: true));
        await tester.pump();
        await tester.pumpWidget(_wrap(_Host(key: host), visible: false));
        await tester.pump();
      }
      expect(host.currentState!.count, 1, reason: '冷卻時間內只算一次');
    });

    testWidgets('canAutoRefresh 說不行就不拉', (tester) async {
      // 時間軸翻過頁、摘要正在手動生成，都會走這條路。
      final host = _HostKey();
      await tester.pumpWidget(_wrap(_Host(key: host, allowed: false)));

      await tester.pump(const Duration(seconds: 61));
      expect(host.currentState!.count, 0);
    });

    testWidgets('畫面拆掉之後計時器不留下來', (tester) async {
      await tester.pumpWidget(_wrap(_Host(key: _HostKey())));
      await tester.pump(const Duration(seconds: 61));
      await tester.pumpWidget(const MaterialApp(home: SizedBox()));
      await tester.pump(const Duration(seconds: 61));
      // 沒清乾淨的話 flutter_test 會在測試結束時丟
      // 「A Timer is still pending even after the widget tree was disposed」。
    });
  });

  group('AsyncView 背景重拉', () {
    testWidgets('換上新 future 時繼續畫舊資料，不退回轉圈', (tester) async {
      // 每 60 秒整頁閃一次轉圈已經夠糟，更糟的是 ListView 被換掉等於整棵重建，
      // 照護者捲到一半的清單會彈回最上面。
      final view = _AsyncHostKey();
      await tester.pumpWidget(_wrap(_AsyncHost(key: view)));
      await tester.pump();
      expect(find.text('第一份'), findsOneWidget);

      view.currentState!.swapIn(_never());
      await tester.pump();

      expect(find.text('第一份'), findsOneWidget);
      expect(find.text('載入中…'), findsNothing);
    });

    testWidgets('第一次載入還是要有轉圈', (tester) async {
      // 上面那條規則不能反過來把 loading 態吃掉：手上沒有任何資料時該畫的就是轉圈。
      final view = _AsyncHostKey();
      await tester.pumpWidget(_wrap(_AsyncHost(key: view, initial: _never())));
      await tester.pump();

      expect(find.text('載入中…'), findsOneWidget);
    });

    testWidgets('上一次是失敗的，重試時要畫轉圈而不是把錯誤留著', (tester) async {
      // 用 Completer 而不是 `Future.error`：後者在 FutureBuilder 訂閱之前就完成了，
      // 錯誤會先被測試框架當成「未處理的例外」攔下來，測試連跑都跑不到斷言。
      final failing = Completer<String>();
      final view = _AsyncHostKey();
      await tester
          .pumpWidget(_wrap(_AsyncHost(key: view, initial: failing.future)));
      failing.completeError(Exception('壞了'));
      await tester.pump();
      expect(find.text('重新載入'), findsOneWidget);

      view.currentState!.swapIn(_never());
      await tester.pump();

      expect(find.text('載入中…'), findsOneWidget);
      expect(find.text('重新載入'), findsNothing);
    });
  });
}

Widget _wrap(Widget child, {bool visible = true}) => MaterialApp(
      home: TickerMode(enabled: visible, child: child),
    );

/// 永遠不完成的 future：模擬「新的一輪已經送出、還沒回來」那一瞬間。
Future<String> _never() => Completer<String>().future;

typedef _HostKey = GlobalKey<_HostState>;

class _Host extends StatefulWidget {
  const _Host({super.key, this.allowed = true});

  final bool allowed;

  @override
  State<_Host> createState() => _HostState();
}

class _HostState extends State<_Host> with AutoRefreshState<_Host> {
  int count = 0;

  @override
  bool get canAutoRefresh => widget.allowed;

  @override
  Future<void> autoRefresh() async => count++;

  @override
  Widget build(BuildContext context) => const SizedBox();
}

typedef _AsyncHostKey = GlobalKey<_AsyncHostState>;

class _AsyncHost extends StatefulWidget {
  const _AsyncHost({super.key, this.initial});

  final Future<String>? initial;

  @override
  State<_AsyncHost> createState() => _AsyncHostState();
}

class _AsyncHostState extends State<_AsyncHost> {
  late Future<String> _future = widget.initial ?? Future<String>.value('第一份');

  // 不寫成 `=> setState(() => _future = next)`：那個箭頭閉包會把賦值結果
  // （Future）回傳出去，setState 收到非 void 的回傳值就會斷言失敗。
  void swapIn(Future<String> next) {
    setState(() {
      _future = next;
    });
  }

  @override
  Widget build(BuildContext context) => AsyncView<String>(
        future: _future,
        onRetry: () {},
        builder: (_, data) => Text(data),
      );
}
