import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:go_router/go_router.dart';
import 'package:uuid/uuid.dart';

import '../../shared/models/caregiver.dart';
import '../../shared/models/elder.dart';
import '../../shared/models/routine.dart';
import '../../shared/services/care_repository.dart';
import '../../shared/services/health_note_ack_store.dart';
import '../../shared/services/notification_service.dart';
import '../../shared/services/session_store.dart';
import '../../shared/widgets/app_card.dart';
import '../../shared/widgets/async_view.dart';
import '../../shared/widgets/auto_refresh.dart';
import '../../shared/widgets/care_header.dart';
import '../../shared/widgets/sign_out_button.dart';
import '../../theme/app_theme.dart';

/// S8 `/care/manage` — 長輩資料與例行公事管理。
///
/// `GET/POST /elders`、`GET/POST/PATCH /routines`。可新增長輩、新增與刪除例行公事
/// （服藥時間、回診、約會）；長輩基本資料目前只有語言可改。
///
/// 寫入端點都要 `client_request_id`：同一個值重送拿到同一筆，不會建出兩筆重複行程
/// （api.md 冪等規則）。送出前產生一次並持有，重試沿用；改內容才換新值。
class EldersScreen extends StatefulWidget {
  const EldersScreen({super.key});

  @override
  State<EldersScreen> createState() => _EldersScreenState();
}

class _EldersScreenState extends State<EldersScreen>
    with AutoRefreshState<EldersScreen> {
  static const _uuid = Uuid();

  late Future<List<Routine>> _future;
  final _routines = <Routine>[];

  @override
  void initState() {
    super.initState();
    _load();
  }

  /// 長輩用講的也能新增行程、改健康狀況，照護者這一頁得跟得上。
  @override
  Future<void> autoRefresh() async {
    try {
      final list = await _fetch();
      if (!mounted) return;
      setState(() => _future = Future.value(list));
    } catch (_) {
      // 靜默：畫面維持上一份成功的資料
    }
  }

  void _load() {
    _future = _fetch();
  }

  Future<List<Routine>> _fetch() async {
    await AppSession.instance.ensureEldersLoaded();
    final elderId = AppSession.instance.selectedElderId;
    // 還沒綁定任何長輩——剛註冊的照護者必然是這個狀態。這裡原本是 `selectedElderId!`，
    // null 時直接丟 Null check operator，整頁被錯誤畫面取代，連同意書與登出都不見了。
    // 「還沒有長輩」是正常狀態不是錯誤，回空清單讓畫面照常畫。
    if (elderId == null) {
      _routines.clear();
      // 沒有長輩就不該有任何提醒留在系統裡（syncRoutines 開頭會 cancelAll）。
      unawaited(NotificationService.instance.syncRoutines(const []));
      return const [];
    }
    final list = await CareRepo.instance.routines(elderId: elderId);
    _routines
      ..clear()
      ..addAll(list);
    // 這裡也要重排：切換長輩會走 onElderChanged → _reload → 這個函式，而提醒排在系統裡，
    // 不換掉的話手機上留著的是上一位長輩的行程。syncRoutines 開頭就 cancelAll，
    // 所以重排一次等於「清掉舊的 + 排上新的」，不必自己先取消。
    unawaited(NotificationService.instance.syncRoutines(list));
    return list;
  }

  void _reload() => setState(_load);

  Future<void> _addRoutine() async {
    final draft = await showModalBottomSheet<_RoutineDraft>(
      context: context,
      isScrollControlled: true,
      backgroundColor: AppColors.cardAlt,
      shape: const RoundedRectangleBorder(borderRadius: AppRadius.voicePanel),
      builder: (_) => const _RoutineForm(),
    );
    if (draft == null || !mounted) return;

    // 冪等鍵：這一次新增從頭到尾用同一個值，重送不會建出第二筆。
    final clientRequestId = _uuid.v4();
    final Routine created;
    try {
      created = await CareRepo.instance.createRoutine(
        clientRequestId: clientRequestId,
        elderId: AppSession.instance.selectedElderId!,
        fields: draft.toJson(),
      );
    } catch (e) {
      // 失敗不能靜悄悄：照護者會以為行程已經建好了，之後也不會再回來看一次。
      if (mounted) _showError('新增行程失敗：$e');
      return;
    }
    if (!mounted) return;

    setState(() => _routines.add(created));
    // 行程變了就重排提醒，否則新增的項目要等下次啟動才會響
    unawaited(NotificationService.instance.syncRoutines(_routines));

    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        backgroundColor: AppColors.barDark,
        content: Text('已新增「${created.title}」',
            style: const TextStyle(color: AppColors.onDark)),
      ),
    );

    if (created.remind) await _ensureNotificationPermission();
  }

  /// 寫入失敗的統一提示。
  void _showError(String message) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        backgroundColor: AppColors.barDark,
        content: Text(message, style: const TextStyle(color: AppColors.onDark)),
      ),
    );
  }

  /// 勾了「要提醒」才問通知權限。
  ///
  /// 原本只有長者的 `/setup` 會問（那裡是「剛設定完長輩資料，這時問要不要提醒吃藥
  /// 是有情境的」），但照護者不走那條路——於是 Android 13+ 的照護者裝置從來沒被
  /// 授權過，syncRoutines 照排、系統直接吞掉，什麼都不會跳。
  ///
  /// 放在「新增一筆要提醒的行程之後」而不是 App 啟動時，是同一套理由：使用者剛表達
  /// 「這件事要提醒我」，這時候要權限最有情境，答應的機率也最高。
  ///
  /// 被拒絕要講出來。提醒排不上但畫面顯示「已新增」，照護者會以為它會響——那比
  /// 一開始就沒有這個功能更糟。
  Future<void> _ensureNotificationPermission() async {
    bool granted;
    try {
      granted = await NotificationService.instance.requestPermission();
    } catch (_) {
      // 平台不支援（web 預覽）就當作沒這回事，不要跳一條看不懂的錯誤給使用者。
      return;
    }
    if (granted || !mounted) return;

    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        backgroundColor: AppColors.barDark,
        duration: const Duration(seconds: 6),
        content: Text('這台手機還沒開啟通知，提醒不會跳出來。可到系統設定開啟。',
            style: Theme.of(context)
                .textTheme
                .bodyMedium
                ?.copyWith(color: AppColors.onDark)),
      ),
    );
  }

  // 語言切換不在這一頁：改由長輩自己在長者端今日頁切（見 elder/widgets/lang_toggle.dart）。
  // 真正知道自己講哪一種話的是長輩本人，而照護者設錯時長輩沒有自救的辦法。
  // 兩邊都能改反而更糟——長者的選擇只寫本機、照護者的寫後端，同時存在就會互相覆蓋，
  // 而長輩那一份必須贏（實際在說話的是他），照護者這顆按下去等於按不動。

  /// 就地換掉 AppSession 裡的那一筆長者。
  ///
  /// 全 App 的長者資料只有 AppSession 一份，改完要就地換掉，否則長者端的語音分流
  /// （AppSession.isHakka）與其他畫面讀到的還是舊值。
  void _replaceElder(Elder updated) {
    final i = AppSession.instance.elders
        .indexWhere((e) => e.elderId == updated.elderId);
    if (i < 0) return;
    setState(() {
      AppSession.instance.elders = [
        ...AppSession.instance.elders.sublist(0, i),
        updated,
        ...AppSession.instance.elders.sublist(i + 1),
      ];
    });
  }

  /// 新增一筆健康註記（`POST /elders/{id}/health_notes`）。
  ///
  /// 走單筆端點而不是 `PATCH` 送整份：這個欄位對話中的 AI 也會寫（api.md），
  /// 整份覆寫會把長輩剛講出來的那一筆一起蓋掉。
  Future<void> _addHealthNote(Elder elder) async {
    final text = await showDialog<String>(
      context: context,
      builder: (ctx) => const _HealthNoteDialog(),
    );
    if (text == null || text.isEmpty || !mounted) return;

    final Elder updated;
    try {
      updated = await CareRepo.instance
          .addHealthNote(elderId: elder.elderId, text: text);
    } catch (e) {
      if (mounted) _showError('新增失敗：$e');
      return;
    }
    if (!mounted) return;

    _replaceElder(updated);
  }

  /// 新增一位家屬。
  ///
  /// 家屬走 `PATCH` 整份取代就夠了，不像 health_notes 要另開單筆端點——
  /// `update_elder_profile` 沒有寫 `family` 的參數（docs/llm_tools.md），
  /// 這個欄位只有照護者在改，沒有併發覆蓋的問題。
  Future<void> _addFamilyMember(Elder elder) async {
    final member = await showDialog<FamilyMember>(
      context: context,
      builder: (ctx) => const _FamilyMemberDialog(),
    );
    if (member == null || !mounted) return;

    await _saveFamily(elder, [...elder.family, member], '新增失敗');
  }

  /// 刪除一位家屬。
  ///
  /// `FamilyMember` 沒有 ID（api.md 的結構就是三個欄位），只能用位置指定。
  /// 這裡沒有併發寫入，位置在送出前不會被別人推移。
  Future<void> _removeFamilyMember(Elder elder, int index) async {
    final member = elder.family[index];
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: AppColors.cardAlt,
        title: Text('刪除「${member.name}」？',
            style: Theme.of(ctx).textTheme.titleMedium),
        content: Text(
          '刪掉之後 AI 跟長輩聊天時不會再提到這位家人。',
          style: Theme.of(ctx).textTheme.bodyLarge,
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            style: TextButton.styleFrom(foregroundColor: AppColors.ink),
            child: const Text('取消'),
          ),
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(true),
            style: TextButton.styleFrom(foregroundColor: AppColors.accentText),
            child: const Text('刪除'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;

    final remaining = [
      for (var i = 0; i < elder.family.length; i++)
        if (i != index) elder.family[i],
    ];
    await _saveFamily(elder, remaining, '刪除失敗');
  }

  Future<void> _saveFamily(
    Elder elder,
    List<FamilyMember> family,
    String errorPrefix,
  ) async {
    final Elder updated;
    try {
      updated = await CareRepo.instance.updateElder(
        elder.elderId,
        {
          'family': [for (final m in family) m.toJson()]
        },
      );
    } catch (e) {
      if (mounted) _showError('$errorPrefix：$e');
      return;
    }
    if (!mounted) return;

    _replaceElder(updated);
  }

  /// 編輯生活習慣（`PATCH /elders/{id}` 的 `habit_note`）。
  ///
  /// **這個欄位對話中的 AI 也會寫**（`update_elder_profile` 的
  /// `habit_note_to_append`，長輩講「我不吃牛肉」就會被接到字串後面）。
  /// 這裡是整份取代，照護者按下儲存時若 AI 剛好補了一句，那句會被蓋掉。
  ///
  /// 前端解不掉：後端把它當一整段字串在拼，沒有 per-item 概念，做不到像
  /// health_notes 那樣各碰各的。所以對話框裡明講這件事，讓照護者知道自己在
  /// 覆寫整段——真正的修法是後端把生活習慣也拆成可單獨增刪的結構。
  Future<void> _editHabitNote(Elder elder) async {
    final text = await showDialog<String>(
      context: context,
      builder: (ctx) => _HabitNoteDialog(initial: elder.habitNote ?? ''),
    );
    if (text == null || !mounted) return;

    final Elder updated;
    try {
      updated = await CareRepo.instance
          .updateElder(elder.elderId, {'habit_note': text});
    } catch (e) {
      if (mounted) _showError('儲存失敗：$e');
      return;
    }
    if (!mounted) return;

    _replaceElder(updated);
  }

  /// 編輯居住地區（`PATCH /elders/{id}` 的 `address_region`）。
  ///
  /// 為什麼照護者要改得了：這個欄位是對話大腦查天氣的依據（後端的
  /// `get_weather_forecast` 工具），長輩問「明天會不會下雨」全靠它。而長輩會搬家、
  /// 也可能是子女幫忙填錯的——初次設定填完就永久唯讀，等於錯了沒人救得回來。
  Future<void> _editAddressRegion(Elder elder) async {
    final region = await showDialog<String>(
      context: context,
      builder: (ctx) =>
          _AddressRegionDialog(initial: elder.addressRegion ?? ''),
    );
    if (region == null || !mounted) return;

    final Elder updated;
    try {
      updated = await CareRepo.instance
          .updateElder(elder.elderId, {'address_region': region});
    } catch (e) {
      if (mounted) _showError('儲存失敗：$e');
      return;
    }
    if (!mounted) return;

    _replaceElder(updated);
  }

  /// 刪除一筆健康註記（`DELETE /elders/{id}/health_notes/{note_id}`）。
  ///
  /// 先問一次再刪：健康資訊刪掉之後照護者不會記得原本寫了什麼，而 AI 補上的那幾筆
  /// 本來就是要照護者判斷去留的，按錯不該直接生效。
  Future<void> _removeHealthNote(Elder elder, HealthNote note) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: AppColors.cardAlt,
        title: Text('刪除「${note.text}」？',
            style: Theme.of(ctx).textTheme.titleMedium),
        content: Text(
          note.source == HealthNoteSource.agent
              ? '這是 AI 從長輩的談話裡記下來的。刪掉之後 AI 不會再參考這一項。'
              : '刪掉之後 AI 不會再參考這一項。',
          style: Theme.of(ctx).textTheme.bodyLarge,
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            style: TextButton.styleFrom(foregroundColor: AppColors.ink),
            child: const Text('取消'),
          ),
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(true),
            style: TextButton.styleFrom(foregroundColor: AppColors.accentText),
            child: const Text('刪除'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;

    final Elder updated;
    try {
      updated = await CareRepo.instance
          .removeHealthNote(elderId: elder.elderId, noteId: note.noteId);
    } catch (e) {
      if (mounted) _showError('刪除失敗：$e');
      return;
    }
    if (!mounted) return;

    _replaceElder(updated);
  }

  /// 刪除一筆例行公事。
  ///
  /// 先問一次再刪：這個動作在畫面上沒有回頭路（不像停用還能再啟用），而列表裡
  /// 每張卡的刪除鈕位置都一樣，按錯的成本是長輩從此收不到那個提醒——而且照護者
  /// 不會馬上發現。
  ///
  /// 每次都要新的 client_request_id（同值代表同一次修改）。
  Future<void> _deleteRoutine(Routine r) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: AppColors.cardAlt,
        title:
            Text('刪除「${r.title}」？', style: Theme.of(ctx).textTheme.titleMedium),
        content: Text(
          '刪掉之後長輩就不會再收到這個提醒。',
          style: Theme.of(ctx).textTheme.bodyLarge,
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            style: TextButton.styleFrom(foregroundColor: AppColors.ink),
            child: const Text('取消'),
          ),
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(true),
            style: TextButton.styleFrom(foregroundColor: AppColors.accentText),
            child: const Text('刪除'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;

    try {
      // 每次按下刪除產一個新的 id。這一顆按鈕沒有自動重試，按完該筆就從清單消失，
      // 所以拿不到同一筆再刪一次的機會；真正需要沿用同一個 id 的是「送出後沒收到
      // 回應、使用者自己再按一次」，那種情況目前會吃 409（後端已經刪掉了）。
      // 要處理得更好就得把 id 按 routine 存起來，等有人真的遇到再說。
      await CareRepo.instance
          .deleteRoutine(r.routineId, clientRequestId: _uuid.v4());
    } catch (e) {
      if (mounted) _showError('刪除失敗：$e');
      return;
    }
    if (!mounted) return;

    setState(() => _routines.removeWhere((e) => e.routineId == r.routineId));
    // 刪掉要立刻讓提醒消失，不能等下次啟動
    unawaited(NotificationService.instance.syncRoutines(_routines));

    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        backgroundColor: AppColors.barDark,
        content: Text('已刪除「${r.title}」',
            style: const TextStyle(color: AppColors.onDark)),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.app,
      body: SafeArea(
        child: Column(
          children: [
            CareHeader(
              title: '管理',
              subtitle: '長輩資料與例行公事',
              onElderChanged: (_) => _reload(),
              // 綁定新的長輩要靠這組 ID（api.md「綁定照護者」），所以它得有一個
              // 固定看得到的入口，不能只存在後端。
              trailing: const _MyIdButton(),
            ),
            Expanded(
              // 同意書與登出放在 AsyncView **外面**，資料載不出來時也要看得到。
              // 這兩個是這一頁唯一的出路：政策裡寫「刪除資料請聯繫家人或管理者」，
              // 而能執行的人就是照護者；載入失敗時他更需要能登出重來。
              // 長者端的今日頁踩過同一個坑（見 today_screen 的同名說明）。
              child: ListView(
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
                children: [
                  AsyncView<List<Routine>>(
                    future: _future,
                    onRetry: _reload,
                    builder: (context, _) {
                      final elder = AppSession.instance.selectedElder;
                      // 只列還在的。停用改成刪除之後就沒有「已停用」這個狀態了。
                      final visible = _routines.where((r) => r.active).toList();

                      // Column 而非 ListView：它已經是外層 ListView 的一個孩子。
                      return Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          // 沒有「新增長輩」：綁定是**長者發起**的——長輩在他自己的
                          // 手機上輸入照護者 ID（`POST /elders/{id}/caregivers`），綁上
                          // 之後 `GET /elders` 就回完整資料，這一頁自然看得到。
                          //
                          // 從照護者這邊 `POST /elders` 造出來的長輩沒有帳號可以登入：
                          // elder_accounts（sub→elder_id）是註冊時寫的，建立長者資料
                          // 不會產生帳號對應。那會是一筆沒人進得去的孤兒資料。
                          const SectionHeader('長輩資料'),
                          const SizedBox(height: AppSpacing.sm),
                          if (elder != null)
                            _ElderProfileCard(
                              elder: elder,
                              onAddNote: () => _addHealthNote(elder),
                              onRemoveNote: (n) => _removeHealthNote(elder, n),
                              onEditHabit: () => _editHabitNote(elder),
                              onEditRegion: () => _editAddressRegion(elder),
                              onAddFamily: () => _addFamilyMember(elder),
                              onRemoveFamily: (i) =>
                                  _removeFamilyMember(elder, i),
                            ),
                          const SizedBox(height: AppSpacing.lg),
                          SectionHeader(
                            '例行公事',
                            trailing: TextButton.icon(
                              onPressed: _addRoutine,
                              style: TextButton.styleFrom(
                                minimumSize: const Size(48, 48),
                                foregroundColor: AppColors.accentText,
                              ),
                              icon: const Icon(Icons.add, size: 18),
                              label: Text('新增',
                                  style: Theme.of(context)
                                      .textTheme
                                      .labelSmall
                                      ?.copyWith(color: AppColors.accentText)),
                            ),
                          ),
                          const SizedBox(height: AppSpacing.sm),
                          if (visible.isEmpty)
                            _EmptyRoutines(onAdd: _addRoutine)
                          else
                            for (final r in visible) ...[
                              _RoutineCard(
                                key: ValueKey(r.routineId),
                                routine: r,
                                onDelete: () => _deleteRoutine(r),
                              ),
                              const SizedBox(height: AppSpacing.md),
                            ],
                        ],
                      );
                    },
                  ),
                  const SizedBox(height: AppSpacing.xl),
                  const _PolicyLink(),
                  const SignOutButton(),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// 政策說明入口。內容不在這裡，是註冊時那頁 [ConsentPolicyScreen] 的同一份。
///
/// 為什麼登入後還要留一個入口：註冊時看過一次就再也找不到，但真正會用到這份文件的
/// 時機在後面——政策裡寫「刪除資料請聯繫照顧你的家人或系統管理者」，執行的人就是
/// 照護者本人。放管理頁而不是長者端：長輩不會來刪帳號，而且長者模式的三個互動額度
/// 要留給主要操作。
class _PolicyLink extends StatelessWidget {
  const _PolicyLink();

  @override
  Widget build(BuildContext context) {
    // 不用 shared 的 TextLink：那是長者規格（24sp / 60dp），放進管理頁會比周圍
    // Material density 的東西大一截。這裡照管理頁自己的 TextButton 走 48dp。
    return Center(
      child: TextButton(
        onPressed: () => context.push('/auth/consent'),
        style: TextButton.styleFrom(
          minimumSize: const Size(48, 48),
          foregroundColor: AppColors.inkSecondary,
        ),
        child: Text(
          '使用者同意機制與資料保留政策',
          style: Theme.of(context).textTheme.labelSmall?.copyWith(
                color: AppColors.inkSecondary,
                decoration: TextDecoration.underline,
                decorationColor: AppColors.inkSecondary,
              ),
        ),
      ),
    );
  }
}

/// 長輩基本資料。健康狀況、生活習慣與家人可改，其餘唯讀。
class _ElderProfileCard extends StatefulWidget {
  const _ElderProfileCard({
    required this.elder,
    required this.onAddNote,
    required this.onRemoveNote,
    required this.onEditHabit,
    required this.onEditRegion,
    required this.onAddFamily,
    required this.onRemoveFamily,
  });

  final Elder elder;

  final VoidCallback onAddNote;
  final ValueChanged<HealthNote> onRemoveNote;
  final VoidCallback onEditHabit;

  /// 編輯居住地區。天氣工具吃這個欄位，錯了長輩問天氣就答不準。
  final VoidCallback onEditRegion;
  final VoidCallback onAddFamily;

  /// 依位置刪除——`FamilyMember` 沒有 ID。
  final ValueChanged<int> onRemoveFamily;

  @override
  State<_ElderProfileCard> createState() => _ElderProfileCardState();
}

class _ElderProfileCardState extends State<_ElderProfileCard> {
  /// 健康狀況是否在編輯模式。
  ///
  /// 刪除鈕常態掛在每一筆上，卡片看起來像隨時準備刪東西，也容易誤觸——
  /// 那是破壞性動作，不該跟閱讀共用同一個畫面。
  bool _editing = false;

  /// 家屬是否在編輯模式。與健康狀況各自獨立：兩個都展開會讓卡片變得很長，
  /// 而照護者一次通常只在改一件事。
  bool _editingFamily = false;

  /// 已經被這位照護者確認過的 AI 記錄（見 [HealthNoteAckStore]）。
  Set<String> _acked = const {};

  @override
  void initState() {
    super.initState();
    _loadAcked();
  }

  @override
  void didUpdateWidget(_ElderProfileCard old) {
    super.didUpdateWidget(old);

    // 換長輩就收起編輯模式：那是「我正在改這一位」的狀態，帶到下一位身上會讓
    // 照護者對著別人的資料看到一排刪除鈕。
    if (old.elder.elderId != widget.elder.elderId) {
      setState(() {
        _editing = false;
        _editingFamily = false;
      });
      _loadAcked();
      return;
    }

    // 註記有增刪就重算已確認清單。比 note_id 而不是比長度——刪一筆的同時
    // AI 補一筆，長度會一樣但內容已經換了。
    final oldIds = old.elder.healthNotes.map((n) => n.noteId).toSet();
    final newIds = widget.elder.healthNotes.map((n) => n.noteId).toSet();
    if (oldIds.length != newIds.length || !oldIds.containsAll(newIds)) {
      _loadAcked();
    }
  }

  Future<void> _loadAcked() async {
    final elderId = widget.elder.elderId;
    Set<String> acked;
    try {
      acked = await HealthNoteAckStore.instance
          .acked(elderId, current: widget.elder.healthNotes);
    } catch (_) {
      // 讀不到就當作全部未確認：多標幾個「新」不會出事，漏標才會。
      return;
    }
    if (!mounted || widget.elder.elderId != elderId) return;
    setState(() => _acked = acked);
  }

  Future<void> _ack(HealthNote note) async {
    await HealthNoteAckStore.instance.ack(widget.elder.elderId, note.noteId);
    if (!mounted) return;
    setState(() => _acked = {..._acked, note.noteId});
  }

  /// AI 記的、而且這位照護者還沒確認過——這幾筆才標「新」。
  ///
  /// 照護者自己填的不標：自己剛加的東西不需要別人提醒。
  bool _isUnread(HealthNote n) =>
      n.source == HealthNoteSource.agent && !_acked.contains(n.noteId);

  @override
  Widget build(BuildContext context) {
    final elder = widget.elder;
    final text = Theme.of(context).textTheme;
    final age =
        elder.birthYear == null ? null : DateTime.now().year - elder.birthYear!;

    return AppCard(
      radius: AppRadius.cardLarge,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                width: 44,
                height: 44,
                decoration: const BoxDecoration(
                    color: AppColors.avatarBg, shape: BoxShape.circle),
                alignment: Alignment.center,
                child: const Icon(Icons.person,
                    size: 24, color: AppColors.avatarFg),
              ),
              const SizedBox(width: AppSpacing.md),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(elder.name, style: text.titleMedium),
                    Text(
                      [
                        if (elder.nickname?.trim().isNotEmpty == true)
                          '暱稱 ${elder.nickname}',
                        if (age != null) '$age 歲',
                      ].join('・'),
                      style: text.bodySmall
                          ?.copyWith(color: AppColors.inkSecondary),
                    ),
                  ],
                ),
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.md),

          // 健康狀況一筆一列而不是擠成一排標籤：每一筆都要放得下來源標示與操作鈕，
          // 而且來源不同的兩種東西長得一樣才是原本的問題所在。
          //
          // 增刪都收在編輯模式裡：平時是純閱讀，要動才進去。一半操作藏起來、
          // 一半露在外面反而更亂，所以「新增」也一起收。
          _ProfileRow(
            label: '健康狀況',
            // 卡片上有兩個「編輯」（另一個是生活習慣），tooltip 講清楚是哪一個——
            // 讀螢幕的人只聽到兩次「編輯」會分不出來。
            trailing: Tooltip(
              message: _editing ? '完成編輯健康狀況' : '編輯健康狀況',
              child: TextButton(
                onPressed: () => setState(() => _editing = !_editing),
                style: TextButton.styleFrom(
                  minimumSize: const Size(48, 48),
                  padding: EdgeInsets.zero,
                  foregroundColor: AppColors.accentText,
                ),
                child: Text(_editing ? '完成' : '編輯',
                    style:
                        text.labelSmall?.copyWith(color: AppColors.accentText)),
              ),
            ),
            // 平時是緊湊的膠囊，按「編輯」才展開成一列一筆。
            // 膠囊排得下三四項而不會把卡片撐高，但塞不下刪除鈕與來源說明——
            // 那些只有要動手時才需要，所以兩種版面各司其職。
            child: _editing
                ? Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      for (final n in elder.healthNotes)
                        _HealthNoteRow(
                          note: n,
                          isNew: _isUnread(n),
                          onDelete: () => widget.onRemoveNote(n),
                          onAck: () => _ack(n),
                        ),
                      Align(
                        alignment: Alignment.centerLeft,
                        child: TextButton.icon(
                          onPressed: widget.onAddNote,
                          style: TextButton.styleFrom(
                            minimumSize: const Size(48, 48),
                            padding: EdgeInsets.zero,
                            foregroundColor: AppColors.accentText,
                          ),
                          icon: const Icon(Icons.add, size: 18),
                          label: Text('新增一項',
                              style: text.labelSmall
                                  ?.copyWith(color: AppColors.accentText)),
                        ),
                      ),
                    ],
                  )
                : elder.healthNotes.isEmpty
                    ? Text('尚未填寫',
                        style:
                            text.bodySmall?.copyWith(color: AppColors.chevron))
                    : Wrap(
                        spacing: AppSpacing.sm,
                        runSpacing: AppSpacing.xs,
                        children: [
                          for (final n in elder.healthNotes)
                            _HealthNotePill(note: n, isNew: _isUnread(n)),
                        ],
                      ),
          ),
          const SizedBox(height: AppSpacing.md),
          // 與健康狀況同一套：平時唯讀，按「編輯」才出現增刪。
          _ProfileRow(
            label: '家屬',
            trailing: Tooltip(
              message: _editingFamily ? '完成編輯家屬' : '編輯家屬',
              child: TextButton(
                onPressed: () =>
                    setState(() => _editingFamily = !_editingFamily),
                style: TextButton.styleFrom(
                  minimumSize: const Size(48, 48),
                  padding: EdgeInsets.zero,
                  foregroundColor: AppColors.accentText,
                ),
                child: Text(_editingFamily ? '完成' : '編輯',
                    style:
                        text.labelSmall?.copyWith(color: AppColors.accentText)),
              ),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                if (elder.family.isEmpty && !_editingFamily)
                  Text('尚未填寫',
                      style:
                          text.bodySmall?.copyWith(color: AppColors.chevron)),
                for (var i = 0; i < elder.family.length; i++)
                  _FamilyMemberRow(
                    member: elder.family[i],
                    editing: _editingFamily,
                    onDelete: () => widget.onRemoveFamily(i),
                  ),
                if (_editingFamily)
                  Align(
                    alignment: Alignment.centerLeft,
                    child: TextButton.icon(
                      onPressed: widget.onAddFamily,
                      style: TextButton.styleFrom(
                        minimumSize: const Size(48, 48),
                        padding: EdgeInsets.zero,
                        foregroundColor: AppColors.accentText,
                      ),
                      icon: const Icon(Icons.add, size: 18),
                      label: Text('新增一位',
                          style: text.labelSmall
                              ?.copyWith(color: AppColors.accentText)),
                    ),
                  ),
              ],
            ),
          ),
          const SizedBox(height: AppSpacing.md),
          // 空的時候也要留著這一列：原本 habitNote 為 null 就整列不見，等於連
          // 「這裡可以填」都看不到——App 建立的長輩永遠是 null，那一列從來沒出現過。
          _ProfileRow(
            label: '生活習慣',
            trailing: Tooltip(
              message: '編輯生活習慣',
              child: TextButton(
                onPressed: widget.onEditHabit,
                style: TextButton.styleFrom(
                  minimumSize: const Size(48, 48),
                  padding: EdgeInsets.zero,
                  foregroundColor: AppColors.accentText,
                ),
                child: Text('編輯',
                    style:
                        text.labelSmall?.copyWith(color: AppColors.accentText)),
              ),
            ),
            child: (elder.habitNote?.trim().isNotEmpty ?? false)
                ? Text(elder.habitNote!, style: text.bodyMedium)
                : Text('尚未填寫',
                    style: text.bodySmall?.copyWith(color: AppColors.chevron)),
          ),
          const SizedBox(height: AppSpacing.md),
          // 居住地區同樣要能改：長輩會搬家，而且這是初次設定時由子女代填的欄位，
          // 填錯的機會不小。錯了的後果是天氣問了答不準（後端 get_weather_forecast
          // 吃這個值），而不是明顯的壞掉，所以更需要一個看得到、改得動的地方。
          _ProfileRow(
            label: '居住地區',
            trailing: Tooltip(
              message: '編輯居住地區',
              child: TextButton(
                onPressed: widget.onEditRegion,
                style: TextButton.styleFrom(
                  minimumSize: const Size(48, 48),
                  padding: EdgeInsets.zero,
                  foregroundColor: AppColors.accentText,
                ),
                child: Text('編輯',
                    style:
                        text.labelSmall?.copyWith(color: AppColors.accentText)),
              ),
            ),
            child: (elder.addressRegion?.trim().isNotEmpty ?? false)
                ? Text(elder.addressRegion!, style: text.bodyMedium)
                : Text('尚未填寫',
                    style: text.bodySmall?.copyWith(color: AppColors.chevron)),
          ),
        ],
      ),
    );
  }
}

/// 一筆健康註記。
///
/// AI 從長輩談話裡記下來的那幾筆要一眼看得出來：它比照護者手填的更可能出錯，
/// 也更需要有人確認。依 CLAUDE.md「狀態不可只靠顏色」，來源用 icon 加文字標示，
/// 不倚賴顏色本身。
/// 平時的緊湊呈現。來源與「新」都要看得出來，但不放操作鈕。
class _HealthNotePill extends StatelessWidget {
  const _HealthNotePill({required this.note, required this.isNew});

  final HealthNote note;
  final bool isNew;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final fromAgent = note.source == HealthNoteSource.agent;
    final date = note.createdAt;

    // 膠囊上的來源只剩一個 icon，讀螢幕的人看不到形狀——語意標籤要把它說出來，
    // 否則「這筆是 AI 記的」對他們等於不存在。
    return Semantics(
      excludeSemantics: true,
      label: [
        if (fromAgent) '來自對話的紀錄',
        note.text,
        if (fromAgent && date != null) '${date.month} 月 ${date.day} 日',
        if (isNew) '尚未確認',
      ].join('，'),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
        decoration: const BoxDecoration(
          color: AppColors.chipSurface,
          borderRadius: BorderRadius.all(AppRadius.pill),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (fromAgent) ...[
              const Icon(Icons.record_voice_over,
                  size: 13, color: AppColors.accentText),
              const SizedBox(width: 4),
            ],
            // 放大字級時讓文字自己換行，不要把膠囊撐出畫面
            Flexible(
              child: Text(
                fromAgent && date != null
                    ? '${note.text}・${date.month}/${date.day}'
                    : note.text,
                style: text.bodySmall,
              ),
            ),
            if (isNew) ...[
              const SizedBox(width: 4),
              const _NewBadge(),
            ],
          ],
        ),
      ),
    );
  }
}

/// 編輯模式的展開呈現：一列一筆，右側放操作鈕。
class _HealthNoteRow extends StatelessWidget {
  const _HealthNoteRow({
    required this.note,
    required this.isNew,
    required this.onDelete,
    required this.onAck,
  });

  final HealthNote note;

  /// AI 記的且還沒被確認過——標「新」，等於一份待辦。
  final bool isNew;

  final VoidCallback onDelete;

  /// 確認這一筆（留著，並清掉「新」）。
  final VoidCallback onAck;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final fromAgent = note.source == HealthNoteSource.agent;
    final date = note.createdAt;

    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Expanded(
          child: Padding(
            padding: const EdgeInsets.only(top: 12, bottom: 4),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    if (fromAgent) ...[
                      const Padding(
                        padding: EdgeInsets.only(top: 2),
                        child: Icon(Icons.record_voice_over,
                            size: 14, color: AppColors.accentText),
                      ),
                      const SizedBox(width: AppSpacing.xs),
                    ],
                    Flexible(child: Text(note.text, style: text.bodyMedium)),
                    if (isNew) ...[
                      const SizedBox(width: AppSpacing.xs),
                      const _NewBadge(),
                    ],
                  ],
                ),
                if (fromAgent)
                  Text(
                    date == null ? '來自對話' : '來自對話・${date.month}/${date.day}',
                    style:
                        text.labelSmall?.copyWith(color: AppColors.accentText),
                  ),
              ],
            ),
          ),
        ),
        // AI 記的那幾筆要有「留著」這個明確動作，否則「新」只會自己消失，
        // 等於沒有人真的確認過。照護者自己填的沒有這個問題。
        if (isNew)
          IconButton(
            onPressed: onAck,
            constraints: const BoxConstraints(minWidth: 48, minHeight: 48),
            padding: EdgeInsets.zero,
            iconSize: 18,
            color: AppColors.accentText,
            tooltip: '確認「${note.text}」',
            icon: const Icon(Icons.check),
          ),
        IconButton(
          onPressed: onDelete,
          // 照護者模式的觸控下限是 48dp（CLAUDE.md）
          constraints: const BoxConstraints(minWidth: 48, minHeight: 48),
          padding: EdgeInsets.zero,
          iconSize: 18,
          color: AppColors.chevron,
          tooltip: '刪除「${note.text}」',
          // 用垃圾桶不用 ✕：這一按是真的刪掉資料，而 ✕ 在列表上常常是
          // 「關閉」「收合」的意思，兩者的後果差很多。
          icon: const Icon(Icons.delete_outline),
        ),
      ],
    );
  }
}

/// 「新」徽章。
///
/// 依 CLAUDE.md「狀態不可只靠顏色」，這裡本身就是文字，不需要另外加 icon。
class _NewBadge extends StatelessWidget {
  const _NewBadge();

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
        decoration: const BoxDecoration(
          color: AppColors.accentText,
          borderRadius: BorderRadius.all(AppRadius.pill),
        ),
        child: Text('新',
            style: Theme.of(context)
                .textTheme
                .labelSmall
                ?.copyWith(color: AppColors.onDark)),
      );
}

/// 新增一筆健康註記的輸入框。回傳去頭尾空白後的文字；取消或空白回 null。
class _HealthNoteDialog extends StatefulWidget {
  const _HealthNoteDialog();

  @override
  State<_HealthNoteDialog> createState() => _HealthNoteDialogState();
}

class _HealthNoteDialogState extends State<_HealthNoteDialog> {
  final _ctrl = TextEditingController();

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  void _submit() {
    final text = _ctrl.text.trim();
    // 後端對空字串回 400，不值得為此跑一趟網路
    Navigator.of(context).pop(text.isEmpty ? null : text);
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return AlertDialog(
      backgroundColor: AppColors.cardAlt,
      title: Text('新增健康狀況', style: text.titleMedium),
      content: TextField(
        controller: _ctrl,
        autofocus: true,
        style: text.bodyLarge,
        textInputAction: TextInputAction.done,
        onSubmitted: (_) => _submit(),
        decoration: InputDecoration(
          hintText: '例如：高血壓',
          hintStyle: text.bodyLarge?.copyWith(color: AppColors.chevron),
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          style: TextButton.styleFrom(foregroundColor: AppColors.ink),
          child: const Text('取消'),
        ),
        TextButton(
          onPressed: _submit,
          style: TextButton.styleFrom(foregroundColor: AppColors.accentText),
          child: const Text('新增'),
        ),
      ],
    );
  }
}

/// 家屬的一列。編輯模式才顯示刪除。
class _FamilyMemberRow extends StatelessWidget {
  const _FamilyMemberRow({
    required this.member,
    required this.editing,
    required this.onDelete,
  });

  final FamilyMember member;
  final bool editing;
  final VoidCallback onDelete;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final label =
        '${member.relation}　${member.name}${member.note == null || member.note!.isEmpty ? '' : '（${member.note}）'}';

    if (!editing) {
      return Padding(
        padding: const EdgeInsets.only(bottom: 2),
        child: Text(label, style: text.bodyMedium),
      );
    }

    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Expanded(
          child: Padding(
            padding: const EdgeInsets.only(top: 14),
            child: Text(label, style: text.bodyMedium),
          ),
        ),
        IconButton(
          onPressed: onDelete,
          // 照護者模式的觸控下限是 48dp（CLAUDE.md）
          constraints: const BoxConstraints(minWidth: 48, minHeight: 48),
          padding: EdgeInsets.zero,
          iconSize: 18,
          color: AppColors.chevron,
          tooltip: '刪除「${member.name}」',
          icon: const Icon(Icons.delete_outline),
        ),
      ],
    );
  }
}

/// 新增一位家屬的輸入框。關係與姓名必填，備註選填；取消回 null。
class _FamilyMemberDialog extends StatefulWidget {
  const _FamilyMemberDialog();

  @override
  State<_FamilyMemberDialog> createState() => _FamilyMemberDialogState();
}

class _FamilyMemberDialogState extends State<_FamilyMemberDialog> {
  final _relationCtrl = TextEditingController();
  final _nameCtrl = TextEditingController();
  final _noteCtrl = TextEditingController();

  /// 缺必填時的提示。送出後才顯示，不要一打開就紅一片。
  String? _error;

  @override
  void dispose() {
    _relationCtrl.dispose();
    _nameCtrl.dispose();
    _noteCtrl.dispose();
    super.dispose();
  }

  void _submit() {
    final relation = _relationCtrl.text.trim();
    final name = _nameCtrl.text.trim();
    if (relation.isEmpty || name.isEmpty) {
      setState(() => _error = '關係和姓名都要填');
      return;
    }
    final note = _noteCtrl.text.trim();
    Navigator.of(context).pop(FamilyMember(
      relation: relation,
      name: name,
      note: note.isEmpty ? null : note,
    ));
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return AlertDialog(
      backgroundColor: AppColors.cardAlt,
      title: Text('新增家屬', style: text.titleMedium),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            TextField(
              controller: _relationCtrl,
              autofocus: true,
              style: text.bodyLarge,
              textInputAction: TextInputAction.next,
              decoration: InputDecoration(
                labelText: '關係',
                hintText: '例如：兒子',
                hintStyle: text.bodyLarge?.copyWith(color: AppColors.chevron),
              ),
            ),
            const SizedBox(height: AppSpacing.sm),
            TextField(
              controller: _nameCtrl,
              style: text.bodyLarge,
              textInputAction: TextInputAction.next,
              decoration: InputDecoration(
                labelText: '姓名',
                hintText: '例如：陳志明',
                hintStyle: text.bodyLarge?.copyWith(color: AppColors.chevron),
              ),
            ),
            const SizedBox(height: AppSpacing.sm),
            TextField(
              controller: _noteCtrl,
              style: text.bodyLarge,
              textInputAction: TextInputAction.done,
              onSubmitted: (_) => _submit(),
              decoration: InputDecoration(
                labelText: '備註（選填）',
                hintText: '例如：在台北工作，每週三來訪',
                hintStyle: text.bodyLarge?.copyWith(color: AppColors.chevron),
              ),
            ),
            if (_error != null) ...[
              const SizedBox(height: AppSpacing.sm),
              Text(_error!,
                  style:
                      text.labelSmall?.copyWith(color: AppColors.accentText)),
            ],
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          style: TextButton.styleFrom(foregroundColor: AppColors.ink),
          child: const Text('取消'),
        ),
        TextButton(
          onPressed: _submit,
          style: TextButton.styleFrom(foregroundColor: AppColors.accentText),
          child: const Text('新增'),
        ),
      ],
    );
  }
}

/// 編輯生活習慣的輸入框。回傳去頭尾空白後的文字；取消回 null。
///
/// 允許存成空字串（等於清空）——`PATCH` 沒有把欄位改回 null 的語意，空字串是
/// 契約內能表達「這裡沒東西」的方式。
/// 編輯居住地區。單行、不給空字串。
///
/// 不做縣市／鄉鎮的下拉選單：那要維護一份全台行政區清單，而且長輩實際住的地方
/// 未必對得上行政區劃（眷村、部落、某某社區）。天氣工具吃的是地名字串，讓照護者
/// 照自己知道的寫比較準。
class _AddressRegionDialog extends StatefulWidget {
  const _AddressRegionDialog({required this.initial});

  final String initial;

  @override
  State<_AddressRegionDialog> createState() => _AddressRegionDialogState();
}

class _AddressRegionDialogState extends State<_AddressRegionDialog> {
  late final _ctrl = TextEditingController(text: widget.initial);

  @override
  void initState() {
    super.initState();
    // 「儲存」在空字串時是停用的，所以每次輸入都要重畫——沒有這個監聽，
    // 原本是空的地區打了字之後按鈕還是灰的，看起來像壞掉。
    _ctrl.addListener(_onChanged);
  }

  void _onChanged() => setState(() {});

  @override
  void dispose() {
    _ctrl.removeListener(_onChanged);
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return AlertDialog(
      backgroundColor: AppColors.cardAlt,
      title: Text('居住地區', style: text.titleMedium),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          TextField(
            controller: _ctrl,
            autofocus: true,
            style: text.bodyLarge,
            decoration: InputDecoration(
              hintText: '例如：台北市大安區',
              hintStyle: text.bodyLarge?.copyWith(color: AppColors.chevron),
            ),
          ),
          const SizedBox(height: AppSpacing.sm),
          Text('※ 長輩問天氣時要靠它',
              style: text.labelSmall?.copyWith(color: AppColors.chevron)),
        ],
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          style: TextButton.styleFrom(foregroundColor: AppColors.ink),
          child: const Text('取消'),
        ),
        TextButton(
          // 空字串不送：api.md 的 PATCH 是部分更新，沒有「清空欄位」的語意，
          // 送空值只會讓後端存一個沒有意義的空地區。
          onPressed: _ctrl.text.trim().isEmpty
              ? null
              : () => Navigator.of(context).pop(_ctrl.text.trim()),
          style: TextButton.styleFrom(foregroundColor: AppColors.accentText),
          child: const Text('儲存'),
        ),
      ],
    );
  }
}

class _HabitNoteDialog extends StatefulWidget {
  const _HabitNoteDialog({required this.initial});

  final String initial;

  @override
  State<_HabitNoteDialog> createState() => _HabitNoteDialogState();
}

class _HabitNoteDialogState extends State<_HabitNoteDialog> {
  late final _ctrl = TextEditingController(text: widget.initial);

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return AlertDialog(
      backgroundColor: AppColors.cardAlt,
      title: Text('生活習慣', style: text.titleMedium),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          TextField(
            controller: _ctrl,
            autofocus: true,
            style: text.bodyLarge,
            minLines: 3,
            maxLines: 6,
            decoration: InputDecoration(
              hintText: '例如：早睡早起，喜歡去公園散步',
              hintStyle: text.bodyLarge?.copyWith(color: AppColors.chevron),
            ),
          ),
          const SizedBox(height: AppSpacing.sm),
          // 存下去會覆寫整段，包含 AI 期間補上的內容——照護者有權知道
          Text('※ AI 也會從長輩的對話補充這裡，儲存會覆蓋整段',
              style: text.labelSmall?.copyWith(color: AppColors.chevron)),
        ],
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          style: TextButton.styleFrom(foregroundColor: AppColors.ink),
          child: const Text('取消'),
        ),
        TextButton(
          onPressed: () => Navigator.of(context).pop(_ctrl.text.trim()),
          style: TextButton.styleFrom(foregroundColor: AppColors.accentText),
          child: const Text('儲存'),
        ),
      ],
    );
  }
}

class _ProfileRow extends StatelessWidget {
  const _ProfileRow({
    required this.label,
    required this.child,
    this.trailing,
  });

  final String label;
  final Widget child;

  /// 這一列的操作（如健康狀況的「編輯」）。放在標題右側，跟內容分開。
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SizedBox(
          width: 64,
          child: Text(label,
              style: text.labelSmall?.copyWith(color: AppColors.inkSecondary)),
        ),
        const SizedBox(width: AppSpacing.sm),
        Expanded(child: child),
        if (trailing != null) trailing!,
      ],
    );
  }
}

class _RoutineCard extends StatelessWidget {
  const _RoutineCard({
    super.key,
    required this.routine,
    required this.onDelete,
  });

  final Routine routine;
  final VoidCallback onDelete;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final category = EventCategory.fromType(routine.type);

    return AppCard.nested(
      padding: const EdgeInsets.all(AppSpacing.md),
      border: Border.all(color: AppColors.border),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            width: 40,
            height: 40,
            decoration: BoxDecoration(
              color: category.bg,
              borderRadius: BorderRadius.circular(11),
            ),
            alignment: Alignment.center,
            child: Icon(_iconFor(routine.type), size: 20, color: category.fg),
          ),
          const SizedBox(width: AppSpacing.md),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  routine.title,
                  style: text.titleSmall,
                ),
                const SizedBox(height: 2),
                Text(_scheduleLabel(routine.schedule),
                    style: text.bodySmall
                        ?.copyWith(color: AppColors.inkSecondary)),
                const SizedBox(height: AppSpacing.xs),
                // Wrap 而非 Row：這一列在 textScaler 2.0 下量出來約 290dp，
                // 但卡片透過 Expanded 只分得到 213dp——差的 77px 就直接 overflow。
                // 換行而不是截斷：「對話中建立」是來源標示，砍掉照護者就分不出
                // 哪些行程是 AI 聽來的。圖示與其標籤綁在同一個 Row 裡不拆散。
                Wrap(
                  crossAxisAlignment: WrapCrossAlignment.center,
                  spacing: AppSpacing.sm,
                  runSpacing: 2,
                  children: [
                    Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Icon(
                          routine.remind
                              ? Icons.notifications_active_outlined
                              : Icons.notifications_off_outlined,
                          size: 14,
                          color: AppColors.chevron,
                        ),
                        const SizedBox(width: 4),
                        Text(routine.remind ? '會提醒' : '不提醒',
                            style: text.bodySmall
                                ?.copyWith(color: AppColors.chevron)),
                      ],
                    ),
                    // 兩種來源都標。原本只標「對話中建立」，自己排的那幾筆是
                    // 空白——但空白讀起來像「還沒載到」而不是「我排的」，
                    // 一整頁混在一起時照護者還是得逐筆回想哪筆是誰弄的。
                    Text(
                      routine.createdBy == 'conversation'
                          ? '· 長者在對話中建立'
                          : '· 照護者建立',
                      style: text.bodySmall?.copyWith(color: AppColors.chevron),
                    ),
                  ],
                ),
              ],
            ),
          ),
          // 用文字而不是只有一個垃圾桶 icon：icon 的語意要靠猜，而這個動作按錯了
          // 沒有回頭路（送出前還會再問一次，見 _deleteRoutine）。
          TextButton(
            onPressed: onDelete,
            style: TextButton.styleFrom(
              minimumSize: const Size(48, 48),
              foregroundColor: AppColors.accentText,
            ),
            child: Text('刪除',
                style:
                    text.labelSmall?.copyWith(color: AppColors.inkSecondary)),
          ),
        ],
      ),
    );
  }

  /// routine `type` 與 events 共用七類（api.md），所以 `safety` 也要有 icon——
  /// 表單雖然不給這個選項，對話中建立的 routine 仍可能是這一類。
  static IconData _iconFor(String type) => switch (type) {
        'medication' => Icons.medication_outlined,
        'diet' => Icons.restaurant_outlined,
        'activity' => Icons.directions_walk,
        'sleep' => Icons.bedtime_outlined,
        'wellbeing' => Icons.favorite_outline,
        'safety' => Icons.shield_outlined,
        _ => Icons.event_note_outlined,
      };
}

class _EmptyRoutines extends StatelessWidget {
  const _EmptyRoutines({required this.onAdd});

  final VoidCallback onAdd;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return AppCard(
      // 虛線感：預告卡用替代底 + 虛線色外框
      color: AppColors.cardAlt,
      border: Border.all(color: AppColors.borderDashed),
      padding: const EdgeInsets.all(AppSpacing.xl),
      child: Column(
        children: [
          const Icon(Icons.event_note_outlined,
              size: 36, color: AppColors.chevron),
          const SizedBox(height: AppSpacing.md),
          Text('還沒有例行公事',
              style: text.bodyLarge?.copyWith(color: AppColors.inkSecondary)),
          const SizedBox(height: AppSpacing.xs),
          Text('例如服藥時間、回診、固定的散步',
              textAlign: TextAlign.center,
              style: text.bodySmall?.copyWith(color: AppColors.chevron)),
          const SizedBox(height: AppSpacing.lg),
          SizedBox(
            height: 48,
            child: FilledButton.icon(
              onPressed: onAdd,
              icon: const Icon(Icons.add, size: 18),
              label: Text('新增例行公事', style: text.labelMedium),
              style: FilledButton.styleFrom(
                backgroundColor: AppColors.accentText,
                foregroundColor: Colors.white,
                shape: const RoundedRectangleBorder(
                  borderRadius: BorderRadius.all(AppRadius.field),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// 表單填出來的內容。
class _RoutineDraft {
  const _RoutineDraft({
    required this.title,
    required this.type,
    required this.schedule,
    required this.remind,
  });

  final String title;
  final String type;
  final RoutineSchedule schedule;
  final bool remind;

  /// 送往 `POST /routines` 的欄位（`client_request_id` 與 `elder_id` 由呼叫端另外帶）。
  Map<String, dynamic> toJson() => {
        'title': title,
        'type': type,
        'schedule': schedule.toJson(),
        'remind': remind,
      };
}

/// 新增例行公事表單。失焦即驗證、錯誤訊息在欄位下方、有明確關閉鈕（§8／§12）。
class _RoutineForm extends StatefulWidget {
  const _RoutineForm();

  @override
  State<_RoutineForm> createState() => _RoutineFormState();
}

class _RoutineFormState extends State<_RoutineForm> {
  final _formKey = GlobalKey<FormState>();
  final _titleCtrl = TextEditingController();

  String _type = 'medication';
  String _freq = 'daily';
  int _weekday = 1;
  TimeOfDay _time = const TimeOfDay(hour: 9, minute: 0);
  bool _remind = true;

  /// routine 的 type 與 events 共用七類，但這裡刻意只給六個選項：`safety`（跌倒、
  /// 走失、詐騙、居家危害）是「發生了什麼」的事件分類，照護者手動排一件安全類的
  /// 例行公事沒有對應情境。對話或後端建立的 safety routine 仍能正常顯示。
  static const _types = [
    ('medication', '服藥'),
    ('diet', '飲食'),
    ('activity', '活動'),
    ('sleep', '睡眠'),
    ('wellbeing', '身心'),
    ('other', '其他'),
  ];

  static const _freqs = [
    ('daily', '每天'),
    ('weekly', '每週'),
    ('once', '單次'),
  ];

  static const _weekdayLabels = ['一', '二', '三', '四', '五', '六', '日'];

  @override
  void dispose() {
    _titleCtrl.dispose();
    super.dispose();
  }

  void _submit() {
    if (!_formKey.currentState!.validate()) return;
    final time =
        '${_time.hour.toString().padLeft(2, '0')}:${_time.minute.toString().padLeft(2, '0')}';
    final now = DateTime.now();
    Navigator.of(context).pop(_RoutineDraft(
      title: _titleCtrl.text.trim(),
      type: _type,
      remind: _remind,
      schedule: switch (_freq) {
        'weekly' =>
          RoutineSchedule(freq: 'weekly', weekday: _weekday, time: time),
        'once' => RoutineSchedule(
            freq: 'once',
            date:
                '${now.year}-${now.month.toString().padLeft(2, '0')}-${now.day.toString().padLeft(2, '0')}',
            time: time),
        _ => RoutineSchedule(freq: 'daily', time: time),
      },
    ));
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Padding(
      padding:
          EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom),
      child: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 20),
          child: Form(
            key: _formKey,
            autovalidateMode: AutovalidateMode.onUserInteraction,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(child: Text('新增例行公事', style: text.titleMedium)),
                    IconButton(
                      onPressed: () => Navigator.of(context).pop(),
                      tooltip: '關閉',
                      icon: const Icon(Icons.close, color: AppColors.ink),
                    ),
                  ],
                ),
                const SizedBox(height: AppSpacing.md),
                Text('要做什麼', style: text.labelMedium),
                const SizedBox(height: AppSpacing.sm),
                TextFormField(
                  controller: _titleCtrl,
                  style: text.bodyLarge,
                  decoration: _decoration('例如：吃血壓藥'),
                  validator: (v) =>
                      (v == null || v.trim().isEmpty) ? '請填寫項目名稱' : null,
                ),
                const SizedBox(height: AppSpacing.lg),
                Text('分類', style: text.labelMedium),
                const SizedBox(height: AppSpacing.sm),
                Wrap(
                  spacing: AppSpacing.sm,
                  runSpacing: AppSpacing.sm,
                  children: [
                    for (final t in _types)
                      _ChoicePill(
                        label: t.$2,
                        selected: _type == t.$1,
                        onTap: () => setState(() => _type = t.$1),
                      ),
                  ],
                ),
                const SizedBox(height: AppSpacing.lg),
                Text('頻率', style: text.labelMedium),
                const SizedBox(height: AppSpacing.sm),
                Wrap(
                  spacing: AppSpacing.sm,
                  children: [
                    for (final f in _freqs)
                      _ChoicePill(
                        label: f.$2,
                        selected: _freq == f.$1,
                        onTap: () => setState(() => _freq = f.$1),
                      ),
                  ],
                ),
                if (_freq == 'weekly') ...[
                  const SizedBox(height: AppSpacing.md),
                  Wrap(
                    spacing: AppSpacing.sm,
                    children: [
                      for (var i = 0; i < 7; i++)
                        _ChoicePill(
                          label: '週${_weekdayLabels[i]}',
                          selected: _weekday == i + 1,
                          onTap: () => setState(() => _weekday = i + 1),
                        ),
                    ],
                  ),
                ],
                const SizedBox(height: AppSpacing.lg),
                Text('時間', style: text.labelMedium),
                const SizedBox(height: AppSpacing.sm),
                SizedBox(
                  height: 48,
                  child: OutlinedButton.icon(
                    onPressed: () async {
                      final picked = await showTimePicker(
                          context: context, initialTime: _time);
                      if (picked != null) setState(() => _time = picked);
                    },
                    icon: const Icon(Icons.schedule, size: 18),
                    label: Text(_time.format(context), style: text.bodyLarge),
                    style: OutlinedButton.styleFrom(
                      foregroundColor: AppColors.ink,
                      side: const BorderSide(color: AppColors.border),
                      shape: const RoundedRectangleBorder(
                        borderRadius: BorderRadius.all(AppRadius.field),
                      ),
                    ),
                  ),
                ),
                const SizedBox(height: AppSpacing.md),
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  value: _remind,
                  onChanged: (v) => setState(() => _remind = v),
                  activeThumbColor: AppColors.accentText,
                  title: Text('到時間提醒長輩', style: text.bodyLarge),
                  subtitle: Text('關掉就只記錄，不發通知',
                      style: text.bodySmall
                          ?.copyWith(color: AppColors.inkSecondary)),
                ),
                const SizedBox(height: AppSpacing.lg),
                SizedBox(
                  width: double.infinity,
                  height: 52,
                  child: FilledButton(
                    onPressed: _submit,
                    style: FilledButton.styleFrom(
                      backgroundColor: AppColors.accentText,
                      foregroundColor: Colors.white,
                      shape: const RoundedRectangleBorder(
                        borderRadius: BorderRadius.all(AppRadius.field),
                      ),
                    ),
                    child: Text('新增', style: text.labelLarge),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  InputDecoration _decoration(String hint) => InputDecoration(
        hintText: hint,
        hintStyle: const TextStyle(color: AppColors.chevron),
        filled: true,
        fillColor: AppColors.card,
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
        errorStyle: const TextStyle(color: Color(0xFF7D281F), fontSize: 13),
        enabledBorder: const OutlineInputBorder(
          borderRadius: BorderRadius.all(AppRadius.field),
          borderSide: BorderSide(color: AppColors.border),
        ),
        focusedBorder: const OutlineInputBorder(
          borderRadius: BorderRadius.all(AppRadius.field),
          borderSide: BorderSide(color: AppColors.accent, width: 2),
        ),
      );
}

class _ChoicePill extends StatelessWidget {
  const _ChoicePill({
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Semantics(
      button: true,
      selected: selected,
      child: InkWell(
        onTap: onTap,
        borderRadius: const BorderRadius.all(AppRadius.pill),
        child: Container(
          constraints: const BoxConstraints(minHeight: 44),
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
          decoration: BoxDecoration(
            color: selected ? AppColors.accentText : Colors.transparent,
            borderRadius: const BorderRadius.all(AppRadius.pill),
            border: Border.all(
              // 未選取走 borderInteractive 而不是 border：後者是輸入框線，
              // 壓在紙色底上只有 1.3:1，看不出這裡有一顆可以按的東西。
              color:
                  selected ? AppColors.accentText : AppColors.borderInteractive,
              width: selected ? 2 : 1,
            ),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              if (selected) ...[
                const Icon(Icons.check, size: 15, color: Colors.white),
                const SizedBox(width: 4),
              ],
              Text(label,
                  style: text.labelSmall?.copyWith(
                      color: selected ? Colors.white : AppColors.inkSecondary)),
            ],
          ),
        ),
      ),
    );
  }
}

String _scheduleLabel(RoutineSchedule s) => switch (s.freq) {
      'daily' => '每天 ${s.time ?? ''}',
      'weekly' => '每週${_weekdayName(s.weekday)} ${s.time ?? ''}',
      'once' => '${s.date ?? ''} ${s.time ?? ''}',
      _ => s.time ?? '',
    };

String _weekdayName(int? w) {
  const names = ['一', '二', '三', '四', '五', '六', '日'];
  if (w == null || w < 1 || w > 7) return '';
  return names[w - 1];
}

/// 頁首右側的「ID」入口——照護者自己的 ID（`GET /me`）。
///
/// 為什麼要有這一顆：綁定第二位長輩、或長輩自己開的帳號，都是由**家人在長輩手機上
/// 輸入自己的 ID**完成（api.md「綁定照護者」）。ID 是後端由 Cognito `sub` 衍生的，
/// 照護者無從得知，App 不給看就等於這條路走不通。
class _MyIdButton extends StatelessWidget {
  const _MyIdButton();

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Semantics(
      button: true,
      label: '我的 ID，點一下查看並複製',
      child: InkWell(
        onTap: () => _showMyIdSheet(context),
        borderRadius: const BorderRadius.all(AppRadius.pill),
        child: Container(
          // 照護者模式觸控下限 44dp（MASTER.md §6）。
          constraints: const BoxConstraints(minHeight: 44, minWidth: 44),
          padding: const EdgeInsets.symmetric(horizontal: 12),
          decoration: BoxDecoration(
            color: AppColors.chipSurface,
            borderRadius: const BorderRadius.all(AppRadius.pill),
            border: Border.all(color: AppColors.border),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.badge_outlined,
                  size: 18, color: AppColors.accentText),
              const SizedBox(width: AppSpacing.xs),
              Text('ID',
                  style:
                      text.labelSmall?.copyWith(color: AppColors.accentText)),
            ],
          ),
        ),
      ),
    );
  }
}

Future<void> _showMyIdSheet(BuildContext context) => showModalBottomSheet<void>(
      context: context,
      backgroundColor: AppColors.cardAlt,
      shape: const RoundedRectangleBorder(borderRadius: AppRadius.voicePanel),
      builder: (_) => const _MyIdSheet(),
    );

/// 我的 ID 面板：顯示 ID、一鍵複製，並說明它要拿去哪裡用。
///
/// 有明確關閉鈕，不只靠往下滑（MASTER.md §12 modal escape）。
class _MyIdSheet extends StatefulWidget {
  const _MyIdSheet();

  @override
  State<_MyIdSheet> createState() => _MyIdSheetState();
}

class _MyIdSheetState extends State<_MyIdSheet> {
  late Future<Caregiver?> _future;

  /// 已複製的回饋刻意不設計成幾秒後消失：面板是使用者主動關的，
  /// 停在畫面上比用 SnackBar 好——SnackBar 會被面板本身蓋住。
  bool _copied = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  void _load() => _future = AppSession.instance.ensureMeLoaded();

  Future<void> _copy(String id) async {
    await Clipboard.setData(ClipboardData(text: id));
    if (!mounted) return;
    setState(() => _copied = true);
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 20),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(child: Text('我的 ID', style: text.titleMedium)),
                IconButton(
                  onPressed: () => Navigator.of(context).pop(),
                  tooltip: '關閉',
                  icon: const Icon(Icons.close, color: AppColors.ink),
                ),
              ],
            ),
            const SizedBox(height: AppSpacing.xs),
            Text(
              '請在長輩的手機上打開「連結家人」，輸入這組 ID，\n您就能看到他每天的狀況。',
              style: text.bodyMedium?.copyWith(color: AppColors.inkSecondary),
            ),
            const SizedBox(height: AppSpacing.lg),
            AsyncView<Caregiver?>(
              future: _future,
              onRetry: () => setState(_load),
              isEmpty: (me) => me == null,
              emptyIcon: Icons.badge_outlined,
              emptyText: '還沒取得您的 ID，請重新登入後再試。',
              builder: (context, me) => _IdCard(
                caregiver: me!,
                copied: _copied,
                onCopy: () => _copy(me.caregiverId),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// ID 本體與複製鈕。
class _IdCard extends StatelessWidget {
  const _IdCard({
    required this.caregiver,
    required this.copied,
    required this.onCopy,
  });

  final Caregiver caregiver;
  final bool copied;
  final VoidCallback onCopy;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        AppCard(
          color: AppColors.nest,
          padding: const EdgeInsets.all(AppSpacing.lg),
          border: Border.all(color: AppColors.border),
          shadows: const [],
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // 後端保證 name 有值，但 demo 的 token 沒有名字可取，所以留空時不畫這一行。
              if (caregiver.name.trim().isNotEmpty) ...[
                Text(caregiver.name.trim(),
                    style: text.bodySmall
                        ?.copyWith(color: AppColors.inkSecondary)),
                const SizedBox(height: AppSpacing.xs),
              ],
              // ID 是英數混合，字距拉開避免 0/o、1/l 看錯；可長按選取，
              // 給複製鈕失效（如某些桌面環境）時留一條路。
              SelectableText(
                caregiver.caregiverId,
                style: text.titleLarge?.copyWith(letterSpacing: 2),
              ),
            ],
          ),
        ),
        const SizedBox(height: AppSpacing.md),
        SizedBox(
          width: double.infinity,
          height: 48,
          child: FilledButton.icon(
            onPressed: onCopy,
            // 狀態不只靠文字：icon 一起換（MASTER.md §6）。
            icon:
                Icon(copied ? Icons.check : Icons.copy_all_outlined, size: 18),
            // labelLarge 自帶 AppColors.ink，直接傳會蓋掉 foregroundColor，
            // 變成深褐字壓朱紅底（2.2:1）。字級要 16/w700 又要白字，只能自己 copyWith。
            label: Text(copied ? '已複製' : '複製 ID',
                style: text.labelLarge?.copyWith(color: Colors.white)),
            style: FilledButton.styleFrom(
              backgroundColor: AppColors.accentText,
              foregroundColor: Colors.white,
              shape: const RoundedRectangleBorder(
                borderRadius: BorderRadius.all(AppRadius.field),
              ),
            ),
          ),
        ),
        if (copied) ...[
          const SizedBox(height: AppSpacing.sm),
          Row(
            children: [
              const Icon(Icons.check_circle,
                  size: 16, color: AppColors.successFg),
              const SizedBox(width: AppSpacing.xs),
              Expanded(
                child: Text('已複製到剪貼簿，可以傳給家人了',
                    style:
                        text.bodySmall?.copyWith(color: AppColors.successFg)),
              ),
            ],
          ),
        ],
      ],
    );
  }
}
