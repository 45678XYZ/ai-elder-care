import 'dart:async';

import 'package:flutter/material.dart';

import 'app_router.dart';
import 'shared/services/auth_service.dart';
import 'shared/services/notification_service.dart';
import 'shared/services/routine_sync.dart';
import 'shared/services/session_store.dart';
import 'theme/app_theme.dart';

Future<void> main() async {
  // 讀取持久化設定前需先初始化 binding。
  WidgetsFlutterBinding.ensureInitialized();
  // 登入狀態要在建 router 之前還原好，redirect 第一次跑就要看到正確的狀態，
  // 否則已登入的人會先閃一下登入頁。
  await AuthService.instance.restore();
  // 順序不能反：長者情境（含「已完成首次設定」）是按帳號存的，
  // 要先還原出登入者的 sub 才讀得到他那一份。
  await AppSession.instance
      .loadForAccount(AuthService.instance.identity?.userId);

  // 通知初始化失敗不該擋住 App 啟動——在不支援本地通知的平台（web 預覽）、
  // 或外掛初始化出問題時，使用者仍然要能用其他功能。
  try {
    await NotificationService.instance.init();
  } catch (_) {
    // 提醒排不上，其餘照常
  }

  runApp(AiElderCareApp());

  // 提醒在啟動時重排，不擋畫面顯示。
  // 放在啟動而不是管理頁：長輩那台手機不會進管理頁，但一樣要收到提醒。
  unawaited(syncReminders());
}

/// 重拉例行公事定義並重排本地提醒。
///
/// 實作在 [RoutineSync.refresh]——同一件事還有另外兩個呼叫點（照護者改動行程、
/// 長輩用講的改動行程），集中在那裡才不會三邊各做一半。
Future<void> syncReminders() => RoutineSync.refresh();

/// App 進入點。
///
/// 登入走 Cognito SDK（不經後端 API），登入後依帳號角色切換：
/// - 長者模式 → lib/elder/
/// - 照護者模式 → lib/caregiver/
///
/// 落點不在這裡決定，交給 app_router 的 redirect：條件有三層（有沒有登入、有沒有宣告
/// 身分、長者有沒有建資料），只有集中判斷才不會有組合漏掉，web 上直接開網址也才擋得住。
class AiElderCareApp extends StatelessWidget {
  AiElderCareApp({super.key});

  final _router = buildRouter();

  @override
  Widget build(BuildContext context) {
    return MaterialApp.router(
      title: '客照e點通',
      debugShowCheckedModeBanner: false,
      theme: buildAppTheme(),
      routerConfig: _router,
    );
  }
}
