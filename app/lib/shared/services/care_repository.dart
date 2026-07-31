import '../config/api_config.dart';
import '../models/api_page.dart';
import '../models/caregiver.dart';
import '../models/chat_reply.dart';
import '../models/daily_summary.dart';
import '../models/elder.dart';
import '../models/life_event.dart';
import '../models/routine.dart';
import '../models/stats.dart';
import 'api_repository.dart';
import 'chat_session.dart';
import 'demo_repository.dart';

/// 畫面取資料的唯一入口——底下可能是真後端（[ApiRepository]）或 demo 假資料
/// （[DemoRepository]），由 [ApiConfig.useBackend] 決定，畫面兩者都不認識。
///
/// **為什麼要這一層**：畫面原本各自寫死 `DemoData.xxx()`，旁邊放一行
/// 「TODO: 後端上線後改為 api.xxx()」。那表示後端上線那天要同時改七個檔案、
/// 而且改完就回不去假資料了——demo 現場真後端一連不上就沒有退路。
/// 收斂成一個介面之後，切換是一個 `--dart-define`，兩邊都隨時可用。
///
/// 方法簽章一律貼著 docs/api.md 的端點（參數名、`elder_id` 必填與否、分頁游標），
/// 不為了讓 demo 好寫而自創形狀——不然真接上時這一層本身又要改一次。
abstract interface class CareRepository {
  // ---- 對話（長者模式）----

  /// `POST /chat` — 送一句話並取得 AI 回覆。
  ///
  /// `session_id` 與 `client_request_id` 由實作代管（見 [ChatSession]）：冪等鍵重送必須
  /// 沿用同一個值，那條規則不該散落在畫面裡。
  ///
  /// 回應的 [ChatReply.routinesUpdated] 為 true 時，呼叫端要走 `RoutineSync.refresh()`
  /// ——長輩用講的完成或新增行程，後端會寫進 routines，但本地通知與畫面是 App 自己的，
  /// 不重整就看不到。
  Future<ChatReply> chat({
    required String elderId,
    required String lang,
    required String text,
  });

  /// `POST /chat/sessions/{id}/close` — 結束目前對話 session。
  ///
  /// 停止免手持互動、離開對話畫面或切換長者前呼叫；會凍結快照並啟動離線事件整理。
  /// 沒有進行中的 session 時什麼都不做。
  Future<void> closeChat();

  // ---- 呼叫者身分 ----

  /// `GET /me` — 照護者自己的身分。
  ///
  /// [sub] 只有 [DemoRepository] 用得到（它得自己從 sub 衍生 ID）；真後端從 token
  /// 認人，這個參數會被忽略。留在簽章上是因為 demo 那條路確實需要它。
  Future<Caregiver> me({required String sub});

  // ---- 長者 ----

  /// `GET /elders` — 照護者可存取的長者（已翻完所有頁）。
  Future<List<Elder>> elders();

  /// `PATCH /elders/{id}` — 部分更新。[fields] 的可用欄位見 docs/api.md。
  Future<Elder> updateElder(String elderId, Map<String, dynamic> fields);

  // ---- 綁定照護者 ----

  /// `POST /elders/{id}/caregivers` — 把一位照護者綁到這位長者（長者本人呼叫）。
  ///
  /// 三種結果都要分得出來：新綁上（`isNew=true`）、早就綁過（`isNew=false`）、
  /// 查無此 ID（丟 [ApiException]，code 為 `CAREGIVER_NOT_FOUND`）。
  /// 第三種是長輩打錯字時唯一的線索，吞掉的話畫面會對著一個不存在的 ID 說連結成功。
  Future<CaregiverLink> linkCaregiver({
    required String elderId,
    required String caregiverId,
  });

  /// `GET /elders/{id}/caregivers` — 已綁定的家人（已翻完所有頁，由舊到新）。
  Future<List<Caregiver>> caregivers({required String elderId});

  // ---- 例行公事 ----

  /// `GET /routines?elder_id=` — 例行公事定義（已翻完所有頁；App 據此排本地通知）。
  Future<List<Routine>> routines({required String elderId});

  /// `POST /routines` — 建立。
  ///
  /// [clientRequestId] 是冪等鍵，由呼叫端產生並持有：後端用它算 `routine_id`，
  /// 同一個值重送拿到同一筆，不會建出兩筆重複行程。
  Future<Routine> createRoutine({
    required String clientRequestId,
    required String elderId,
    required Map<String, dynamic> fields,
  });

  /// `PATCH /routines/{id}` — 修改／停用。
  ///
  /// [clientRequestId] **每次修改都要新的一個**（同值代表同一次修改）。
  Future<Routine> updateRoutine(
    String routineId, {
    required String clientRequestId,
    required Map<String, dynamic> fields,
  });

  /// `GET /routines?elder_id=&date=` — 當日行程視圖。
  Future<DailyRoutineView> dailyRoutines({
    required String elderId,
    required String date,
  });

  /// `POST /routines/{id}/complete` — 手動確認完成。
  ///
  /// 收整個 [occurrence] 而不只是 id：真後端只需要 `routine_id`，但 demo 要靠原本的
  /// 標題與排程時間才能回一筆長得像樣的 occurrence 給畫面顯示。
  Future<RoutineOccurrence> completeRoutine(
    RoutineOccurrence occurrence, {
    String? date,
  });

  // ---- 每日摘要（照護者）----

  /// `GET /summaries` — 每日摘要（一頁）。[from]/[to] 為日期，含首尾，預設最近 7 天。
  Future<ApiPage<DailySummary>> summaries({
    required String elderId,
    String? from,
    String? to,
    String? nextToken,
  });

  /// `POST /summaries/generate` — 手動生成（Demo 用；可能回 partial）。
  Future<DailySummary> generateSummary({
    required String elderId,
    String? date,
  });

  // ---- 生活事件（時間軸，照護者）----

  /// `GET /events` — 生活事件（一頁）。[nextToken] 原樣帶回，不解析。
  Future<ApiPage<LifeEvent>> events({
    required String elderId,
    String? from,
    String? to,
    String? type,
    String? nextToken,
  });

  // ---- 統計（照護者）----

  /// `GET /stats` — 今日／期間互動與例行公事完成率。
  Future<Stats> stats({required String elderId, int days});
}

/// 目前生效的 [CareRepository]。畫面一律透過 `CareRepo.instance` 取資料。
abstract final class CareRepo {
  static CareRepository? _override;
  static CareRepository? _configured;

  /// 依 [ApiConfig.useBackend] 決定實作，第一次讀到才建——沒用到的那個實作
  /// （以及它底下的 HTTP client）不會生出來。
  static CareRepository get instance =>
      _override ??
      (_configured ??=
          ApiConfig.useBackend ? ApiRepository() : DemoRepository());

  /// 測試用：換掉資料來源；傳 null 還原成 [ApiConfig.useBackend] 決定的那個。
  ///
  /// 還原時連目前這個實例一起丟掉，下次取用會建一個全新的——[DemoRepository]
  /// 是有狀態的（新增／停用的行程留在記憶體裡），沿用同一個實例會讓前一個測試的
  /// 寫入流進下一個測試，變成看執行順序而定的失敗。
  ///
  /// 因此每個會碰到資料的測試都該在 setUp 呼叫一次 `CareRepo.overrideWith(null)`。
  static void overrideWith(CareRepository? repo) {
    _override = repo;
    if (repo == null) _configured = null;
  }
}
