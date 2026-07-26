import 'dart:async';

import 'package:flutter/material.dart';

import 'app_router.dart';
import 'shared/services/demo_data.dart';
import 'shared/services/notification_service.dart';
import 'shared/services/session_store.dart';
import 'theme/app_theme.dart';

Future<void> main() async {
  // 讀取持久化設定前需先初始化 binding。
  WidgetsFlutterBinding.ensureInitialized();
  await AppSession.instance.load();
  await NotificationService.instance.init();

  runApp(AiElderCareApp());

  // 提醒在啟動時重排，不擋畫面顯示。
  // 放在啟動而不是管理頁：長輩那台手機不會進管理頁，但一樣要收到提醒。
  unawaited(syncReminders());
}

/// 重拉例行公事定義並重排本地提醒。
///
/// 呼叫時機：App 啟動、照護者改動行程、`/chat` 回 `routines_updated=true`。
/// 失敗不擋任何流程——提醒排不上不該讓 App 起不來，下次啟動會再試一次。
Future<void> syncReminders() async {
  try {
    // TODO: 後端上線後改為 api.getRoutines(elderId: AppSession.instance.selectedElderId!)
    final routines = await DemoData.routines();
    await NotificationService.instance.syncRoutines(routines);
  } catch (_) {
    // 忽略：沒有提醒比起不了 App 好
  }
}

/// App 進入點。
///
/// 登入走 Cognito SDK（不經後端 API），登入後依帳號角色切換：
/// - 長者模式 → lib/elder/
/// - 照護者模式 → lib/caregiver/
///
/// 落點：首次安裝進 S1 首次設定，之後啟動直接進 S2 角色選擇（見 session_store）。
/// 正式流程由登入角色決定。
class AiElderCareApp extends StatelessWidget {
  AiElderCareApp({super.key});

  final _router = buildRouter(
    initialLocation: AppSession.instance.setupDone ? '/' : '/setup',
  );

  @override
  Widget build(BuildContext context) {
    return MaterialApp.router(
      title: '智慧長照陪伴',
      debugShowCheckedModeBanner: false,
      theme: buildAppTheme(),
      routerConfig: _router,
    );
  }
}
