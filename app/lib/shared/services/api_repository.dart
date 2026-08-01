import '../models/api_page.dart';
import '../models/caregiver.dart';
import '../models/chat_reply.dart';
import '../models/daily_summary.dart';
import '../models/elder.dart';
import '../models/life_event.dart';
import '../models/routine.dart';
import '../models/stats.dart';
import 'api_client.dart';
import 'auth_service.dart';
import 'care_repository.dart';
import 'chat_session.dart';

/// [CareRepository] 的真後端實作——把介面轉成 docs/api.md 的端點呼叫。
///
/// 這一層刻意**很薄**：不做快取、不做重試、不改資料形狀。畫面看到的差異只該來自
/// 後端本身，不該來自這裡多做了什麼——否則 demo 資料跑得動、真後端跑不動時，
/// 會分不清是誰的問題。
///
/// token 從 [AuthService] 拿（尚未接上 Cognito 前是 null，[ApiClient] 會不帶
/// Authorization header）；接上之後這裡不用改。
class ApiRepository implements CareRepository {
  ApiRepository({ApiClient? client})
      : _api = client ??
            ApiClient(tokenProvider: () async => AuthService.instance.idToken);

  final ApiClient _api;

  /// 目前這位長者的對話 session。切換長者要換一個（session 屬於特定長者，混用會 403）。
  ChatSession? _chat;

  // ---- 對話 ----

  @override
  Future<ChatReply> chat({
    required String elderId,
    required String lang,
    String? text,
    String? audioBase64,
  }) async {
    var chat = _chat;
    // 換了長者或換了語言就重建：session 綁長者，lang 則決定後端 ASR/TTS 走哪一條，
    // 兩者都是建構時決定的，沿用舊的等於把新的一句話送進上一位長輩的 session。
    if (chat == null || chat.elderId != elderId || chat.lang != lang) {
      await closeChat();
      chat = _chat = ChatSession(api: _api, elderId: elderId, lang: lang);
    }
    return chat.send(text: text, audioBase64: audioBase64);
  }

  @override
  Future<void> closeChat() async {
    final chat = _chat;
    if (chat == null) return;
    _chat = null;
    await chat.close();
  }

  @override
  Future<Caregiver> me({required String sub}) => _api.getMe();

  // ---- 長者 ----

  @override
  Future<List<Elder>> elders() => _api.getAllElders();

  @override
  Future<Elder> createElder(Map<String, dynamic> fields) =>
      _api.createElder(fields);

  @override
  Future<Elder> updateElder(String elderId, Map<String, dynamic> fields) =>
      _api.updateElder(elderId, fields);

  @override
  Future<Elder> addHealthNote({
    required String elderId,
    required String text,
  }) =>
      _api.addHealthNote(elderId, text);

  @override
  Future<Elder> removeHealthNote({
    required String elderId,
    required String noteId,
  }) =>
      _api.removeHealthNote(elderId, noteId);

  // ---- 綁定照護者 ----

  @override
  Future<CaregiverLink> linkCaregiver({
    required String elderId,
    required String caregiverId,
  }) =>
      _api.linkCaregiver(elderId: elderId, caregiverId: caregiverId);

  @override
  Future<List<Caregiver>> caregivers({required String elderId}) =>
      _api.getCaregivers(elderId);

  // ---- 例行公事 ----

  @override
  Future<List<Routine>> routines({required String elderId}) =>
      _api.getRoutines(elderId: elderId);

  @override
  Future<Routine> createRoutine({
    required String clientRequestId,
    required String elderId,
    required Map<String, dynamic> fields,
  }) =>
      _api.createRoutine(
        clientRequestId: clientRequestId,
        fields: {'elder_id': elderId, ...fields},
      );

  @override
  Future<Routine> updateRoutine(
    String routineId, {
    required String clientRequestId,
    required Map<String, dynamic> fields,
  }) =>
      _api.updateRoutine(routineId,
          clientRequestId: clientRequestId, fields: fields);

  @override
  Future<void> deleteRoutine(String routineId,
          {required String clientRequestId}) =>
      _api.deleteRoutine(routineId);

  @override
  Future<DailyRoutineView> dailyRoutines({
    required String elderId,
    required String date,
  }) =>
      _api.getDailyRoutines(elderId: elderId, date: date);

  @override
  Future<RoutineOccurrence> completeRoutine(
    RoutineOccurrence occurrence, {
    String? date,
  }) =>
      // 後端只要 routine_id；occurrence 的其餘欄位是給 demo 那條路用的。
      _api.completeRoutine(occurrence.routineId, date: date);

  // ---- 摘要、事件、統計 ----

  @override
  Future<ApiPage<DailySummary>> summaries({
    required String elderId,
    String? from,
    String? to,
    String? nextToken,
  }) =>
      _api.getSummaries(
          elderId: elderId, from: from, to: to, nextToken: nextToken);

  @override
  Future<DailySummary> generateSummary({
    required String elderId,
    String? date,
  }) =>
      _api.generateSummary(elderId: elderId, date: date);

  @override
  Future<ApiPage<LifeEvent>> events({
    required String elderId,
    String? from,
    String? to,
    String? type,
    String? nextToken,
  }) =>
      _api.getEvents(
          elderId: elderId,
          from: from,
          to: to,
          type: type,
          nextToken: nextToken);

  @override
  Future<Stats> stats({required String elderId, int days = 7}) =>
      _api.getStats(elderId: elderId, days: days);
}
