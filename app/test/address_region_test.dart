import 'package:e_hakka_care/caregiver/screens/elders_screen.dart';
import 'package:e_hakka_care/shared/services/care_repository.dart';
import 'package:e_hakka_care/shared/services/session_store.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// 居住地區的編輯（`PATCH /elders/{id}` 的 `address_region`）。
///
/// 這個欄位原本**建完就永久唯讀**：只有初次設定填得到，管理頁連顯示都沒有。
/// 但它是後端 `get_weather_forecast` 的依據，長輩問「明天會不會下雨」全靠它——
/// 而且是子女代填的欄位，填錯機會不小，長輩也會搬家。
///
/// 錯了的後果不是明顯的壞掉，是天氣答得不準，所以更需要一個看得到、改得動的地方。
void main() {
  setUp(() {
    SharedPreferences.setMockInitialValues({});
    AppSession.instance
      ..elders = const []
      ..linkedCaregivers = const []
      ..selectedElderId = null;
    CareRepo.overrideWith(null);
  });

  /// 不用 pumpAndSettle：面板的 loading 轉圈會一直排新的一幀，settle 永遠不會發生。
  Future<void> pumpManage(WidgetTester tester) async {
    const size = Size(390, 3000);
    tester.view
      ..physicalSize = size
      ..devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(const MaterialApp(
      home: MediaQuery(
        data: MediaQueryData(size: size, disableAnimations: true),
        child: EldersScreen(),
      ),
    ));
    await tester.pump(const Duration(seconds: 1));
    await tester.pump(const Duration(seconds: 1));
  }

  Future<void> openDialog(WidgetTester tester) async {
    await tester.ensureVisible(find.byTooltip('編輯居住地區'));
    await tester.pump(const Duration(milliseconds: 300));
    await tester.tap(find.byTooltip('編輯居住地區'));
    await tester.pump(const Duration(milliseconds: 500));
  }

  testWidgets('管理頁看得到居住地區，也有編輯入口', (tester) async {
    await pumpManage(tester);
    expect(find.text('居住地區'), findsOneWidget);
    expect(find.byTooltip('編輯居住地區'), findsOneWidget);
  });

  testWidgets('儲存後畫面就換成新地區', (tester) async {
    await pumpManage(tester);
    await openDialog(tester);

    await tester.enterText(find.byType(TextField).last, '新竹縣竹東鎮');
    await tester.tap(find.text('儲存'));
    for (var i = 0; i < 6; i++) {
      await tester.pump(const Duration(milliseconds: 300));
    }

    expect(AppSession.instance.elders.first.addressRegion, '新竹縣竹東鎮');
  });

  testWidgets('取消就什麼都不動', (tester) async {
    await pumpManage(tester);
    final before = AppSession.instance.elders.first.addressRegion;

    await openDialog(tester);
    await tester.enterText(find.byType(TextField).last, '不該被存下來');
    await tester.tap(find.text('取消'));
    await tester.pump(const Duration(milliseconds: 500));

    expect(AppSession.instance.elders.first.addressRegion, before);
  });

  testWidgets('清空之後不能儲存——PATCH 沒有「清空欄位」的語意', (tester) async {
    await pumpManage(tester);
    await openDialog(tester);

    await tester.enterText(find.byType(TextField).last, '   ');
    await tester.pump(const Duration(milliseconds: 300));

    final save = tester.widget<TextButton>(
      find.ancestor(of: find.text('儲存'), matching: find.byType(TextButton)),
    );
    expect(save.onPressed, isNull, reason: '空地區送出去只會存一個沒有意義的空值');
  });
}
