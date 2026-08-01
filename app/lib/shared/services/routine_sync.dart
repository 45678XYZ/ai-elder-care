import 'package:flutter/foundation.dart';

import 'care_repository.dart';
import 'notification_service.dart';
import 'session_store.dart';

/// 「行程有變動」的全 App 廣播，以及變動之後要做的兩件善後。
///
/// **為什麼需要它**：長者模式的兩個 tab 掛在 `StatefulNavigationShell` 底下，切走再
/// 切回來狀態是留著的、`initState` 不會重跑。於是長輩在聊天頁講完「藥吃了」、後端
/// 把那筆行程標成完成，切回今日畫面看到的仍是切走前那份資料——demo 要證明的
/// 「沒有人按過完成鍵，它自己打勾了」正好卡在這裡。
///
/// 用 [ValueNotifier] 而不是引入狀態管理框架：CLAUDE.md 明訂引入前要全隊一致，
/// 而這裡要的東西只有「有事發生了」這一個訊號，一個版本號就夠。
abstract final class RoutineSync {
  /// 行程版本號。每次行程有變動就 +1，畫面監聽它決定要不要重拉。
  ///
  /// 只帶版本號、不帶內容：各畫面要的形狀不一樣（今日畫面要當日 occurrence、
  /// 管理頁要定義列表），塞在這裡等於逼所有人共用同一份不合身的資料。
  static final ValueNotifier<int> revision = ValueNotifier<int>(0);

  /// 廣播「行程變了」，並重排本地提醒。
  ///
  /// 呼叫時機：App 啟動、照護者改動行程、`/chat` 回 `routines_updated=true`
  /// （長輩用講的新增或完成行程——後端會寫進 routines，但本地通知是 App 自己排的，
  /// 不重排就要等下次啟動才生效）。
  ///
  /// 先廣播再重排：重排要打網路、可能慢也可能失敗，而畫面重拉不該等它。
  /// 整段失敗不擋任何流程——提醒排不上不該讓長輩看不到行程。
  static Future<void> refresh() async {
    revision.value++;
    try {
      await AppSession.instance.ensureEldersLoaded();
      final elderId = AppSession.instance.selectedElderId;
      // 還不知道要排誰的行程（未登入、或帳號還沒有長者資料）就跳過，
      // 等使用者登入後由管理頁那條路重排。
      if (elderId == null) return;
      final routines = await CareRepo.instance.routines(elderId: elderId);
      await NotificationService.instance.syncRoutines(routines);
    } catch (_) {
      // 忽略：沒有提醒比起不了 App 好
    }
  }

  /// 測試用：把版本號歸零。這是全域狀態，不重設會讓監聽者的比對跨測試失準。
  @visibleForTesting
  static void resetForTest() => revision.value = 0;
}
