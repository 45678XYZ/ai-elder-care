import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:go_router/go_router.dart';
import 'package:uuid/uuid.dart';

import '../../shared/models/caregiver.dart';
import '../../shared/models/elder.dart';
import '../../shared/models/routine.dart';
import '../../shared/services/care_repository.dart';
import '../../shared/services/notification_service.dart';
import '../../shared/services/session_store.dart';
import '../../shared/widgets/app_card.dart';
import '../../shared/widgets/async_view.dart';
import '../../shared/widgets/care_header.dart';
import '../../shared/widgets/sign_out_button.dart';
import '../../theme/app_theme.dart';

/// S8 `/care/manage` — 長輩資料與例行公事管理。
///
/// `GET /elders`、`GET/POST/PATCH /routines`。長輩基本資料唯讀顯示，
/// 例行公事可新增與停用（服藥時間、回診、約會）。
///
/// 寫入端點都要 `client_request_id`：同一個值重送拿到同一筆，不會建出兩筆重複行程
/// （api.md 冪等規則）。送出前產生一次並持有，重試沿用；改內容才換新值。
class EldersScreen extends StatefulWidget {
  const EldersScreen({super.key});

  @override
  State<EldersScreen> createState() => _EldersScreenState();
}

class _EldersScreenState extends State<EldersScreen> {
  static const _uuid = Uuid();

  late Future<List<Routine>> _future;
  final _routines = <Routine>[];

  @override
  void initState() {
    super.initState();
    _load();
  }

  void _load() {
    _future = _fetch();
  }

  Future<List<Routine>> _fetch() async {
    await AppSession.instance.ensureEldersLoaded();
    final list = await CareRepo.instance
        .routines(elderId: AppSession.instance.selectedElderId!);
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

  /// 新增一位長輩（`POST /elders`）。
  ///
  /// 這是照護者上線後的第一步（demo Act 1）：建立者的 token `sub` 會被後端自動加進
  /// `caregiver_ids`，所以建完立刻就看得到這位長輩，不必再走一次綁定。
  ///
  /// 建完切換過去並重載：下一步一定是幫這位長輩排行程，停在上一位身上很容易
  /// 沒注意就把行程加到別人頭上。
  Future<void> _addElder() async {
    final draft = await showModalBottomSheet<_ElderDraft>(
      context: context,
      isScrollControlled: true,
      backgroundColor: AppColors.cardAlt,
      shape: const RoundedRectangleBorder(borderRadius: AppRadius.voicePanel),
      builder: (_) => const _ElderForm(),
    );
    if (draft == null || !mounted) return;

    final Elder created;
    try {
      created = await AppSession.instance.createElder(draft.toJson());
    } catch (e) {
      if (mounted) _showError('新增長輩失敗：$e');
      return;
    }
    if (!mounted) return;

    _reload();
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        backgroundColor: AppColors.barDark,
        content: Text('已新增「${created.name}」，接下來可以幫他排行程',
            style: const TextStyle(color: AppColors.onDark)),
      ),
    );
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

  /// 切換長輩的語音語言（`PATCH /elders/{id}` 的 `lang_preference`）。
  ///
  /// 這是全 App 唯一能改語言的地方：介面文字一律華語、長者端不提供切換，
  /// 這個值只決定長輩說話與聽回覆走華語還是客語（客語裝置端無法辨識，改走錄音送後端）。
  Future<void> _changeLang(Elder elder, String lang) async {
    if (elder.langPreference == lang) return;

    final Elder updated;
    try {
      updated = await CareRepo.instance
          .updateElder(elder.elderId, {'lang_preference': lang});
    } catch (e) {
      if (mounted) _showError('切換語言失敗：$e');
      return;
    }
    if (!mounted) return;

    // 全 App 的長者資料只有 AppSession 一份，改完要就地換掉那一筆，
    // 否則長者端的語音分流（AppSession.isHakka）讀到的還是舊值。
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
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        backgroundColor: AppColors.barDark,
        content: Text(
            '已改為${updated.langPreference == 'hak' ? '客語' : '華語'}，長輩下次說話時生效',
            style: const TextStyle(color: AppColors.onDark)),
      ),
    );
  }

  /// 停用／啟用。PATCH 每次修改都要**新的** client_request_id（同值代表同一次修改）。
  Future<void> _toggleActive(Routine r) async {
    final Routine updated;
    try {
      updated = await CareRepo.instance.updateRoutine(
        r.routineId,
        clientRequestId: _uuid.v4(),
        fields: {'active': !r.active},
      );
    } catch (e) {
      if (mounted) _showError('${r.active ? '停用' : '啟用'}失敗：$e');
      return;
    }
    if (!mounted) return;

    final i = _routines.indexWhere((e) => e.routineId == updated.routineId);
    if (i < 0) return;
    setState(() => _routines[i] = updated);
    // 停用要立刻讓提醒消失，不能等下次啟動
    unawaited(NotificationService.instance.syncRoutines(_routines));
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
              child: AsyncView<List<Routine>>(
                future: _future,
                onRetry: _reload,
                builder: (context, _) {
                  final elder = AppSession.instance.selectedElder;
                  final active = _routines.where((r) => r.active).toList();
                  final paused = _routines.where((r) => !r.active).toList();

                  return ListView(
                    padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
                    children: [
                      SectionHeader(
                        '長輩資料',
                        trailing: TextButton.icon(
                          onPressed: _addElder,
                          style: TextButton.styleFrom(
                            minimumSize: const Size(48, 48),
                            foregroundColor: AppColors.accentText,
                          ),
                          icon: const Icon(Icons.person_add_alt, size: 18),
                          label: Text('新增長輩',
                              style: Theme.of(context)
                                  .textTheme
                                  .labelSmall
                                  ?.copyWith(color: AppColors.accentText)),
                        ),
                      ),
                      const SizedBox(height: AppSpacing.sm),
                      if (elder != null)
                        _ElderProfileCard(
                          elder: elder,
                          onLangChanged: (lang) => _changeLang(elder, lang),
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
                      if (_routines.isEmpty)
                        _EmptyRoutines(onAdd: _addRoutine)
                      else ...[
                        for (final r in active) ...[
                          _RoutineCard(
                            key: ValueKey(r.routineId),
                            routine: r,
                            onToggle: () => _toggleActive(r),
                          ),
                          const SizedBox(height: AppSpacing.md),
                        ],
                        if (paused.isNotEmpty) ...[
                          const SizedBox(height: AppSpacing.sm),
                          const SectionHeader('已停用'),
                          const SizedBox(height: AppSpacing.sm),
                          for (final r in paused) ...[
                            _RoutineCard(
                              key: ValueKey(r.routineId),
                              routine: r,
                              onToggle: () => _toggleActive(r),
                            ),
                            const SizedBox(height: AppSpacing.md),
                          ],
                        ],
                      ],
                      const SizedBox(height: AppSpacing.xl),
                      const _PolicyLink(),
                      const SignOutButton(),
                    ],
                  );
                },
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

/// 長輩基本資料。除了語音語言之外都是唯讀顯示。
class _ElderProfileCard extends StatelessWidget {
  const _ElderProfileCard({required this.elder, required this.onLangChanged});

  final Elder elder;

  /// 切換語音語言（`lang_preference`）。這是全 App 唯一能改它的地方。
  final ValueChanged<String> onLangChanged;

  @override
  Widget build(BuildContext context) {
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
          const SizedBox(height: AppSpacing.lg),

          // 語音語言——全 App 唯一能改的地方（長者端不提供切換）
          _ProfileRow(
            label: '說話語言',
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    _LangOption(
                      label: '華語',
                      selected: elder.langPreference != 'hak',
                      onTap: () => onLangChanged('zh-TW'),
                    ),
                    const SizedBox(width: AppSpacing.sm),
                    _LangOption(
                      label: '客語',
                      selected: elder.langPreference == 'hak',
                      onTap: () => onLangChanged('hak'),
                    ),
                  ],
                ),
                const SizedBox(height: AppSpacing.xs),
                Text('※ 影響語音辨識',
                    style: text.bodySmall?.copyWith(color: AppColors.chevron)),
              ],
            ),
          ),
          const SizedBox(height: AppSpacing.md),

          if (elder.healthNotes.isNotEmpty) ...[
            _ProfileRow(
              label: '健康狀況',
              child: Wrap(
                spacing: AppSpacing.sm,
                runSpacing: AppSpacing.xs,
                children: [
                  for (final n in elder.healthNotes)
                    Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 10, vertical: 5),
                      decoration: const BoxDecoration(
                        color: AppColors.chipSurface,
                        borderRadius: BorderRadius.all(AppRadius.pill),
                      ),
                      child: Text(n, style: text.bodySmall),
                    ),
                ],
              ),
            ),
            const SizedBox(height: AppSpacing.md),
          ],
          if (elder.family.isNotEmpty) ...[
            _ProfileRow(
              label: '家屬',
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  for (final f in elder.family)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 2),
                      child: Text(
                        '${f.relation}　${f.name}${f.note == null ? '' : '（${f.note}）'}',
                        style: text.bodyMedium,
                      ),
                    ),
                ],
              ),
            ),
            const SizedBox(height: AppSpacing.md),
          ],
          if (elder.habitNote != null)
            _ProfileRow(
              label: '生活習慣',
              child: Text(elder.habitNote!, style: text.bodyMedium),
            ),
        ],
      ),
    );
  }
}

/// 語言選項。選中同時用實心底與勾表示，不只靠顏色（MASTER.md §6）。
class _LangOption extends StatelessWidget {
  const _LangOption({
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
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
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

class _ProfileRow extends StatelessWidget {
  const _ProfileRow({required this.label, required this.child});

  final String label;
  final Widget child;

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
      ],
    );
  }
}

class _RoutineCard extends StatelessWidget {
  const _RoutineCard({
    super.key,
    required this.routine,
    required this.onToggle,
  });

  final Routine routine;
  final VoidCallback onToggle;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final category = EventCategory.fromType(routine.type);
    final paused = !routine.active;

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
              color: paused ? AppColors.track : category.bg,
              borderRadius: BorderRadius.circular(11),
            ),
            alignment: Alignment.center,
            child: Icon(_iconFor(routine.type),
                size: 20, color: paused ? AppColors.chevron : category.fg),
          ),
          const SizedBox(width: AppSpacing.md),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  routine.title,
                  style: text.titleSmall?.copyWith(
                    color: paused ? AppColors.inkSecondary : AppColors.ink,
                  ),
                ),
                const SizedBox(height: 2),
                Text(_scheduleLabel(routine.schedule),
                    style: text.bodySmall
                        ?.copyWith(color: AppColors.inkSecondary)),
                const SizedBox(height: AppSpacing.xs),
                Row(
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
                        style:
                            text.bodySmall?.copyWith(color: AppColors.chevron)),
                    if (routine.createdBy == 'conversation') ...[
                      const SizedBox(width: AppSpacing.sm),
                      Text('· 對話中建立',
                          style: text.bodySmall
                              ?.copyWith(color: AppColors.chevron)),
                    ],
                  ],
                ),
              ],
            ),
          ),
          // 停用／啟用：文字按鈕而非只有開關，狀態不靠單一視覺線索
          TextButton(
            onPressed: onToggle,
            style: TextButton.styleFrom(
              minimumSize: const Size(48, 48),
              foregroundColor: AppColors.accentText,
            ),
            child: Text(paused ? '啟用' : '停用',
                style: text.labelSmall?.copyWith(
                    color: paused
                        ? AppColors.accentText
                        : AppColors.inkSecondary)),
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
/// 新增長輩的表單內容（`POST /elders` 的 request 欄位，見 docs/api.md）。
class _ElderDraft {
  const _ElderDraft({
    required this.name,
    required this.nickname,
    required this.lang,
    required this.healthNotes,
    required this.family,
  });

  final String name;
  final String nickname;
  final String lang;
  final List<String> healthNotes;
  final List<FamilyMember> family;

  /// 只帶公開欄位：`elder_id`、`caregiver_ids`、`created_at`、`updated_at` 是
  /// server-owned，傳了後端回 400 `INVALID_PARAMETER`。
  ///
  /// 空字串不送，讓後端套它自己的預設（api.md：未提供的 `health_notes`、`family`
  /// 由後端補 `[]`），而不是送一堆空值進去。
  Map<String, dynamic> toJson() => {
        'name': name,
        if (nickname.isNotEmpty) 'nickname': nickname,
        'lang_preference': lang,
        if (healthNotes.isNotEmpty) 'health_notes': healthNotes,
        if (family.isNotEmpty)
          'family': [
            for (final m in family)
              {
                'relation': m.relation,
                'name': m.name,
                if (m.note != null && m.note!.isNotEmpty) 'note': m.note,
              },
          ],
      };
}

/// 新增長輩的表單。
///
/// 欄位取捨照 demo Act 1 的實際輸入：姓名、暱稱、語言、健康狀況、家人。
/// 出生年、性別、居住地區 api.md 有但這裡不收——照護者現場輸入的欄位愈多愈慢，
/// 而那三個目前沒有任何畫面在用；需要時走 `PATCH /elders/{id}` 補。
class _ElderForm extends StatefulWidget {
  const _ElderForm();

  @override
  State<_ElderForm> createState() => _ElderFormState();
}

class _ElderFormState extends State<_ElderForm> {
  final _formKey = GlobalKey<FormState>();
  final _nameCtrl = TextEditingController();
  final _nicknameCtrl = TextEditingController();

  /// 健康狀況與家人用「一行一筆」的多行輸入，不做動態增減列。
  ///
  /// 動態列在手機上要處理新增、刪除、捲動與鍵盤遮擋，欄位一多就變成一堆小按鈕；
  /// 而這兩項的內容本來就是短句，換行輸入對照護者更快，也不會有「按了加號卻沒填」
  /// 留下的空列。家人一行寫「關係,姓名,備註」，逗號分隔。
  final _healthCtrl = TextEditingController();
  final _familyCtrl = TextEditingController();

  String _lang = 'zh-TW';

  @override
  void dispose() {
    _nameCtrl.dispose();
    _nicknameCtrl.dispose();
    _healthCtrl.dispose();
    _familyCtrl.dispose();
    super.dispose();
  }

  /// 一行一筆，去掉空白行。
  static List<String> _lines(String raw) => [
        for (final l in raw.split('\n'))
          if (l.trim().isNotEmpty) l.trim(),
      ];

  /// 「關係,姓名,備註」→ [FamilyMember]。關係與姓名缺一不可，備註選填。
  ///
  /// 分隔符同時接受半形與全形逗號：照護者在手機上打中文時輸入法給的是全形，
  /// 只認半形的話會整行被當成關係、姓名變空的。
  static List<FamilyMember> _parseFamily(String raw) {
    final out = <FamilyMember>[];
    for (final line in _lines(raw)) {
      final parts = line.split(RegExp('[,，]')).map((p) => p.trim()).toList();
      if (parts.length < 2 || parts[0].isEmpty || parts[1].isEmpty) continue;
      out.add(FamilyMember(
        relation: parts[0],
        name: parts[1],
        note: parts.length > 2 && parts[2].isNotEmpty ? parts[2] : null,
      ));
    }
    return out;
  }

  void _submit() {
    if (!_formKey.currentState!.validate()) return;
    Navigator.of(context).pop(_ElderDraft(
      name: _nameCtrl.text.trim(),
      nickname: _nicknameCtrl.text.trim(),
      lang: _lang,
      healthNotes: _lines(_healthCtrl.text),
      family: _parseFamily(_familyCtrl.text),
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
                    Expanded(child: Text('新增長輩', style: text.titleMedium)),
                    IconButton(
                      onPressed: () => Navigator.of(context).pop(),
                      tooltip: '關閉',
                      icon: const Icon(Icons.close, color: AppColors.ink),
                    ),
                  ],
                ),
                const SizedBox(height: AppSpacing.md),
                Text('姓名', style: text.labelMedium),
                const SizedBox(height: AppSpacing.sm),
                TextFormField(
                  controller: _nameCtrl,
                  style: text.bodyLarge,
                  decoration: _elderDecoration('例如：陳阿蘭'),
                  validator: (v) =>
                      (v == null || v.trim().isEmpty) ? '請填寫長輩姓名' : null,
                ),
                const SizedBox(height: AppSpacing.lg),
                Text('稱呼', style: text.labelMedium),
                const SizedBox(height: AppSpacing.sm),
                TextFormField(
                  controller: _nicknameCtrl,
                  style: text.bodyLarge,
                  decoration: _elderDecoration('例如：阿蘭嬤（AI 會這樣叫他）'),
                ),
                const SizedBox(height: AppSpacing.lg),
                Text('說話的語言', style: text.labelMedium),
                const SizedBox(height: AppSpacing.sm),
                Wrap(
                  spacing: AppSpacing.sm,
                  children: [
                    for (final l in const [('zh-TW', '華語'), ('hak', '客語')])
                      _ChoicePill(
                        label: l.$2,
                        selected: _lang == l.$1,
                        onTap: () => setState(() => _lang = l.$1),
                      ),
                  ],
                ),
                const SizedBox(height: AppSpacing.lg),
                Text('健康狀況', style: text.labelMedium),
                const SizedBox(height: AppSpacing.sm),
                TextFormField(
                  controller: _healthCtrl,
                  style: text.bodyLarge,
                  minLines: 2,
                  maxLines: 4,
                  decoration: _elderDecoration('一行一項\n例如：高血壓'),
                ),
                const SizedBox(height: AppSpacing.lg),
                Text('家人', style: text.labelMedium),
                const SizedBox(height: AppSpacing.sm),
                TextFormField(
                  controller: _familyCtrl,
                  style: text.bodyLarge,
                  minLines: 2,
                  maxLines: 4,
                  decoration: _elderDecoration('一行一位，用逗號分開\n例如：兒子,陳志明,在台北工作'),
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
                    child: Text('建立', style: text.labelLarge),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  InputDecoration _elderDecoration(String hint) => InputDecoration(
        hintText: hint,
        hintStyle: const TextStyle(color: AppColors.chevron),
        filled: true,
        fillColor: AppColors.card,
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
