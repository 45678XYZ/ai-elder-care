import 'dart:convert';
import 'dart:math';

import 'package:shared_preferences/shared_preferences.dart';

import '../config/api_config.dart';
import 'auth_backend.dart';
import 'cognito_auth_backend.dart';
import 'demo/demo_auth_backend.dart';
import 'notification_service.dart';
import 'session_store.dart';

/// 使用者角色。判定方式與後端 `backend/src/shared/auth.py` 一致：
/// ID token 帶 `elder_id` → 長者，否則照護者。
enum UserRole { elder, caregiver }

/// 從 Cognito ID token 解出的呼叫者身分（對應後端的 `Caller`）。
class CognitoIdentity {
  const CognitoIdentity({
    required this.role,
    required this.userId,
    this.elderId,
  });

  final UserRole role;

  /// Cognito `sub`；照護者即綁定於 `elders.caregiver_ids` 的值。
  final String? userId;

  /// 長者才有；照護者為 null。
  final String? elderId;
}

/// Cognito 認證服務。
///
/// 註冊／登入直接走 Cognito SDK（不經後端 API，見 docs/framework.md）；取得 **ID token**
/// 給 [ApiClient] 當 Bearer，並依 token 的 `elder_id` claim 決定進長者或照護者模式——
/// 判定與後端 `auth.py` 完全一致。
///
/// 實際跟 Cognito 講話的是 [backend]（[CognitoAuthBackend]），本類只管登入狀態的
/// 持久化、角色判定與註冊時暫存資料的兌現。
class AuthService {
  AuthService();

  /// 全域單例。登入狀態是整個 App 共用的（router 分流、ApiClient 取 token 都要），
  /// 不該讓各畫面各持一份。
  static final AuthService instance = AuthService();

  /// 實際跟 Cognito 講話的實作。預設由 [defaultAuthBackend] 依設定決定；
  /// 測試可直接覆寫成假的。
  AuthBackend backend = defaultAuthBackend();

  /// 持久化的 key。登入狀態要跨啟動保留——長輩不會每天重新登入一次。
  static const _kIdToken = 'auth_id_token';
  static const _kChosenRole = 'auth_chosen_role';
  static const _kChosenRoleSub = 'auth_chosen_role_sub';

  /// 註冊時宣告、但還沒有 token 可綁定的身分，key 前綴。
  ///
  /// 為什麼要按 email 存而不是直接用 [chooseRole]：註冊當下還沒登入，拿不到 Cognito
  /// `sub`，而 [chooseRole] 記的是「哪個 sub 選了什麼」。email 是註冊那一刻唯一能識別
  /// 帳號的東西，所以先寄放在 email 底下，等第一次登入拿到 sub 再轉正。
  static const _kPendingRolePrefix = 'auth_pending_role_';

  String? _idToken;

  /// 使用者在角色選擇頁宣告過的身分；沒選過為 null。
  UserRole? _chosenRole;

  /// [_chosenRole] 是哪個 Cognito `sub` 選的。
  ///
  /// 一定要記 sub：同一台裝置換人登入時，上一個人的選擇不能沿用——
  /// 否則照護者的手機借長輩登入，會直接被丟進照護者模式。
  String? _chosenRoleSub;

  /// 上次拿到的 Cognito ID token；未登入為 null。
  ///
  /// 這一份**可能已經過期**——它只用來解身分（[identity]），claim 讀得出來就夠。
  /// 要拿去打 API 請用 [freshIdToken]。
  String? get idToken => _idToken;

  bool get isSignedIn => _idToken != null;

  /// 取一份現在確定有效的 ID token 給 [ApiClient] 當 Bearer；沒登入為 null。
  ///
  /// Cognito 的 ID token 效期只有一小時，而登入狀態是跨啟動保留的，所以不能沿用
  /// [signIn] 當下那一份——每次呼叫 API 前都問一次 backend，它會在過期時用 refresh
  /// token 換新（見 [AuthBackend.currentIdToken]）。
  ///
  /// backend 回 null（refresh token 也過期了）時退回 [_idToken]：拿一份過期 token 去打
  /// 會換到 401，而 401 至少是明確的「請重新登入」；不帶 Authorization header 反而會被
  /// API Gateway 當成匿名請求，錯誤訊息更難懂。
  Future<String?> freshIdToken() async {
    final fresh = await backend.currentIdToken();
    if (fresh != null && fresh != _idToken) {
      _idToken = fresh;
      final p = await SharedPreferences.getInstance();
      await p.setString(_kIdToken, fresh);
    }
    return fresh ?? _idToken;
  }

  /// App 啟動時還原登入狀態。
  ///
  /// token 解不出身分（過期格式、寫壞、換版）就當作沒登入並清掉：帶著一個讀不出角色的
  /// token 進 App 只會在後面每一次 API 呼叫都失敗，不如在這裡就要求重新登入。
  Future<void> restore() async {
    final p = await SharedPreferences.getInstance();
    final token = p.getString(_kIdToken);
    if (token != null && parseIdentity(token) == null) {
      await p.remove(_kIdToken);
      _idToken = null;
    } else {
      _idToken = token;
    }

    final role = p.getString(_kChosenRole);
    _chosenRole = switch (role) {
      'elder' => UserRole.elder,
      'caregiver' => UserRole.caregiver,
      _ => null,
    };
    _chosenRoleSub = p.getString(_kChosenRoleSub);
  }

  /// 登入並記住 token。回傳解析後的身分，讓呼叫端據此決定進哪個模式。
  ///
  /// token 解不出身分時視為未登入——寧可讓使用者重試，也不要帶著一個
  /// 讀不出角色的 token 進到某個模式。
  Future<CognitoIdentity> signIn({
    required String email,
    required String password,
  }) async {
    final token = await backend.signIn(email: email, password: password);
    final parsed = parseIdentity(token);
    if (parsed == null) {
      _idToken = null;
      throw AuthException.of(AuthErrorCode.unknown);
    }
    _idToken = token;
    final p = await SharedPreferences.getInstance();
    await p.setString(_kIdToken, token);

    // 先設好 _idToken 再處理註冊時暫存的資料：轉正時要綁這次 token 的 sub。
    await _consumePendingSignUpData(email: email, identity: parsed);
    return parsed;
  }

  /// 登出：清掉 token、本機宣告的身分、已排定的提醒，以及**這個帳號**在本機留下的狀態。
  ///
  /// 一起清 [AppSession] 是必要的：長者資料與「已完成首次設定」目前還存在本機，
  /// 下一個登入的人可能是別人（同一支手機借用、demo 換帳號），殘留下來他會看到
  /// 上一個人的稱呼，或被當成已經設定過而跳過建立資料。
  ///
  /// 提醒也必須一起取消，理由更重：本地通知排在**系統**裡，不受登入狀態影響，
  /// App 沒開也照響。不取消的話，上一位長者的吃藥提醒會繼續在這支手機上跳出來——
  /// 那是別人的用藥資訊。下一個人登入時 main 的 syncReminders 會重排他自己的，
  /// 所以這裡只管清乾淨，不必補排。
  Future<void> signOut() async {
    // sub 要在清掉 token 之前取——清掉之後就問不出這次登出的是誰了。
    final sub = identity?.userId;

    // 通知平台不可用（web 預覽、測試環境）不該讓人登不出去：登出的重點是清掉憑證，
    // 而排不了提醒的平台本來也就沒有提醒會殘留。
    try {
      await NotificationService.instance.cancelAll();
    } catch (_) {
      // 沒有提醒可取消，或平台不支援；照常往下登出
    }

    await backend.signOut();
    _idToken = null;
    _chosenRole = null;
    _chosenRoleSub = null;
    final p = await SharedPreferences.getInstance();
    await p.remove(_kIdToken);
    await p.remove(_kChosenRole);
    await p.remove(_kChosenRoleSub);

    await AppSession.instance.clearForAccount(sub);
  }

  /// 這次登入實際該用哪個角色分流；null 代表「還沒宣告過身分」，要先問。
  ///
  /// 順序有意義：token 的 `elder_id` claim 是後端認定的事實，永遠優先；沒有 claim 時才
  /// 看本機宣告過的身分，而且必須是**這個帳號**選的（sub 相符）才算。
  UserRole? get effectiveRole {
    final id = identity;
    if (id == null) return null;
    if (id.elderId != null) return UserRole.elder;
    if (_chosenRole != null && _chosenRoleSub == id.userId) return _chosenRole;
    return null;
  }

  /// 記下使用者在角色選擇頁宣告的身分（連同宣告者的 sub）。
  Future<void> chooseRole(UserRole role) async {
    _chosenRole = role;
    _chosenRoleSub = identity?.userId;
    final p = await SharedPreferences.getInstance();
    await p.setString(
        _kChosenRole, role == UserRole.elder ? 'elder' : 'caregiver');
    final sub = _chosenRoleSub;
    if (sub == null) {
      await p.remove(_kChosenRoleSub);
    } else {
      await p.setString(_kChosenRoleSub, sub);
    }
  }

  /// 記下註冊當下宣告的身分，暫時掛在 email 底下（見 [_kPendingRolePrefix]）。
  ///
  /// 在註冊頁就問身分，是為了省掉登入後多一頁「請問你是？」；代價是那個時間點還沒有
  /// token，所以不能直接用 [chooseRole]（它綁 sub）。真正轉正的時機在 [signIn]。
  Future<void> declarePendingRole({
    required String email,
    required UserRole role,
  }) async {
    final p = await SharedPreferences.getInstance();
    await p.setString(
        _pendingRoleKey(email), role == UserRole.elder ? 'elder' : 'caregiver');

    // ---- demo 專用接線（正式版由後端負責）----
    //
    // 讓 demo 的長者登入後 token 真的帶 elder_id claim，走的是與正式版**完全相同**的
    // 判定路徑（[effectiveRole] 先看 claim），而不是靠本機宣告假裝成長者——否則
    // demo 走得通、接上 Cognito 反而壞掉。
    //
    // 正式版對應的是首次設定那一步：`POST /elders` 帶 `self_register=true`，後端同時
    // 寫入 elder_accounts (sub→elder_id)，之後 pre-token-generation trigger 才注得出
    // elder_id claim（見 backend/src/handlers/elders.py）。因為 claim 是登入當下發的，
    // 自註冊的長輩要重新登入一次才會拿到。
    final b = backend;
    if (role == UserRole.elder && b is DemoAuthBackend) {
      b.markAsElder(email: email, elderId: _newDemoElderId());
    }
  }

  /// 把註冊流程中按 email 暫存的東西一次兌現到這個帳號。
  ///
  /// 現在有兩樣：宣告的身分（[declarePendingRole]）與長輩基本資料
  /// （`AppSession.savePendingSetup`）。兩者是**同一個時序問題**——註冊當下還沒有 sub，
  /// 只能先寄放在 email 底下——所以兌現的時機也該是同一個，集中在這裡才不會有一邊漏掉
  /// （例如日後只改了其中一條路徑的觸發點）。
  Future<void> _consumePendingSignUpData({
    required String email,
    required CognitoIdentity identity,
  }) async {
    await _consumePendingRole(email: email, identity: identity);

    // 沒有 sub 就沒有帳號可以掛，暫存項留著等下一次登入（token 少了 sub 是異常狀況）。
    final sub = identity.userId;
    if (sub != null) {
      await AppSession.instance
          .consumePendingSetup(email: email, accountId: sub);
    }
  }

  /// 把 [declarePendingRole] 寄放的身分兌現，並清掉暫存項（只兌現一次）。
  ///
  /// token 有 `elder_id` 時不採用暫存值：claim 是後端認定的事實，本機宣告沒有資格覆寫它
  /// （[effectiveRole] 的優先順序也是這樣）。此時暫存項一樣清掉——它想達成的事已經成立了，
  /// 留著只會在下次登入被重複套用。
  Future<void> _consumePendingRole({
    required String email,
    required CognitoIdentity identity,
  }) async {
    final p = await SharedPreferences.getInstance();
    final key = _pendingRoleKey(email);
    final raw = p.getString(key);
    if (raw == null) return;

    if (identity.elderId == null) {
      final role = switch (raw) {
        'elder' => UserRole.elder,
        'caregiver' => UserRole.caregiver,
        _ => null,
      };
      if (role != null) await chooseRole(role);
    }
    await p.remove(key);
  }

  /// email 一律 trim + 轉小寫，與 [DemoAuthBackend] 的正規化一致——
  /// 註冊打「A@Example.com 」、登入打「a@example.com」要指到同一筆暫存。
  static String _pendingRoleKey(String email) =>
      '$_kPendingRolePrefix${email.trim().toLowerCase()}';

  /// demo 用的 elder_id，格式對齊 docs/framework.md 的 `eld_<12-lowercase-hex>`。
  /// 正式版由後端產生（`"eld_" + uuid4().hex[:12]`），App 不得指定。
  static String _newDemoElderId() {
    final rnd = Random();
    final hex =
        List.generate(12, (_) => '0123456789abcdef'[rnd.nextInt(16)]).join();
    return 'eld_$hex';
  }

  /// 目前登入者的身分（角色 + sub + elderId）；未登入或 token 格式不對為 null。
  CognitoIdentity? get identity {
    final token = _idToken;
    return token == null ? null : parseIdentity(token);
  }

  /// 解析 ID token 的 claims → 身分。與後端 `auth.py` 一致：有 `elder_id` 為長者，
  /// 否則照護者。token 格式不對時回 null。
  ///
  /// 只認 `elder_id` 這一個 claim：它由 pre-token-generation trigger 注入
  /// （見 `backend/src/handlers/pre_token_generation.py`），不是 Cognito 自訂屬性，
  /// 所以沒有 `custom:elder_id` 這種寫法。
  static CognitoIdentity? parseIdentity(String idToken) {
    final claims = _decodeJwtClaims(idToken);
    if (claims == null) return null;

    final raw = claims['elder_id'];
    final elderId = (raw is String && raw.isNotEmpty) ? raw : null;
    final sub = claims['sub'];

    return CognitoIdentity(
      role: elderId != null ? UserRole.elder : UserRole.caregiver,
      userId: sub is String ? sub : null,
      elderId: elderId,
    );
  }

  /// 解 JWT 的 payload（中段）為 claims map；格式不對回 null。
  ///
  /// 只解碼、不驗簽章——token 由 Cognito 簽發、API Gateway 的 Cognito authorizer 負責驗證，
  /// App 端僅需讀 claims 決定 UI 導向。
  static Map<String, dynamic>? _decodeJwtClaims(String jwt) {
    final parts = jwt.split('.');
    if (parts.length != 3) return null;
    try {
      final payload =
          utf8.decode(base64Url.decode(base64Url.normalize(parts[1])));
      final decoded = jsonDecode(payload);
      return decoded is Map<String, dynamic> ? decoded : null;
    } catch (_) {
      return null;
    }
  }

  // ---- 角色來源 ----

  // elder_id claim 的來源已經接起來了：首次設定的 `POST /elders` 帶
  // `self_register=true`，後端寫入 elder_accounts (sub→elder_id)，pre-token-generation
  // trigger 下次發 token 時才注得進 claim。**下次**是關鍵——自註冊當下手上那張 token
  // 還沒有 claim，這段期間靠 [effectiveRole] 退回本機宣告撐著，畫面仍進長者模式，
  // 資料也存取得到（`POST /elders` 會把建立者自己綁進 `caregiver_ids`，等於自己是
  // 自己的照護者）。
  //
  // TODO(backend): 照護者身分只存在本機（[_kChosenRole]），換裝置或清除 App 資料就會再被問
  //   一次「請問你是？」。正解是後端記錄使用者的 role，App 端只讀不猜。
}

/// 依設定決定登入要走真 Cognito 還是 demo 假帳號。
///
/// 判準是 [ApiConfig.cognitoUserPoolId] 有沒有給：不帶 `--dart-define` 直接
/// `flutter run` 就是純本機的假流程（demo 當天真後端連不上時的退路，
/// 見 [ApiConfig.useBackend] 的說明），帶了才會真的連上 AWS。
///
/// 兩個開關刻意分開：[ApiConfig.useBackend] 管資料要不要走真 API，這裡管登入。
/// 合成一個的話沒辦法「用真帳號登入、但畫面資料走假的」——排查是誰壞掉時很需要這一格。
AuthBackend defaultAuthBackend() => ApiConfig.cognitoUserPoolId.isEmpty
    ? DemoAuthBackend()
    : CognitoAuthBackend(
        userPoolId: ApiConfig.cognitoUserPoolId,
        clientId: ApiConfig.cognitoAppClientId,
      );
