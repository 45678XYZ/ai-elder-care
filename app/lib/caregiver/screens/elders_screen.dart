import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:go_router/go_router.dart';
import 'package:uuid/uuid.dart';

import '../../shared/models/caregiver.dart';
import '../../shared/models/elder.dart';
import '../../shared/models/routine.dart';
import '../../shared/services/demo_data.dart';
import '../../shared/services/notification_service.dart';
import '../../shared/services/session_store.dart';
import '../../shared/widgets/app_card.dart';
import '../../shared/widgets/async_view.dart';
import '../../shared/widgets/care_header.dart';
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
    // TODO: 後端上線後改為 api.getRoutines(elderId: AppSession.instance.selectedElderId!)
    final list = await DemoData.routines();
    _routines
      ..clear()
      ..addAll(list);
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
    // TODO: 後端上線後改為
    //   api.createRoutine(clientRequestId: clientRequestId, fields: draft.toJson())
    setState(() {
      _routines.add(Routine(
        routineId: 'rtn_${clientRequestId.substring(0, 8)}',
        elderId: AppSession.instance.selectedElderId ?? DemoData.elderId,
        title: draft.title,
        type: draft.type,
        schedule: draft.schedule,
        remind: draft.remind,
        createdBy: 'caregiver',
        createdAt: DateTime.now(),
      ));
    });
    // 行程變了就重排提醒，否則新增的項目要等下次啟動才會響
    unawaited(NotificationService.instance.syncRoutines(_routines));

    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        backgroundColor: AppColors.barDark,
        content: Text('已新增「${draft.title}」',
            style: const TextStyle(color: AppColors.onDark)),
      ),
    );
  }

  /// 切換長輩的語音語言（`PATCH /elders/{id}` 的 `lang_preference`）。
  ///
  /// 這是全 App 唯一能改語言的地方：介面文字一律華語、長者端不提供切換，
  /// 這個值只決定長輩說話與聽回覆走華語還是客語（客語裝置端無法辨識，改走錄音送後端）。
  void _changeLang(Elder elder, String lang) {
    if (elder.langPreference == lang) return;
    // TODO: 後端上線後改為
    //   api.updateElder(elder.elderId, {'lang_preference': lang})
    final i = AppSession.instance.elders
        .indexWhere((e) => e.elderId == elder.elderId);
    if (i < 0) return;
    setState(() {
      AppSession.instance.elders = [
        ...AppSession.instance.elders.sublist(0, i),
        Elder(
          elderId: elder.elderId,
          name: elder.name,
          nickname: elder.nickname,
          birthYear: elder.birthYear,
          gender: elder.gender,
          langPreference: lang,
          addressRegion: elder.addressRegion,
          healthNotes: elder.healthNotes,
          family: elder.family,
          habitNote: elder.habitNote,
          createdAt: elder.createdAt,
          updatedAt: DateTime.now(),
        ),
        ...AppSession.instance.elders.sublist(i + 1),
      ];
    });
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        backgroundColor: AppColors.barDark,
        content: Text('已改為${lang == 'hak' ? '客語' : '華語'}，長輩下次說話時生效',
            style: const TextStyle(color: AppColors.onDark)),
      ),
    );
  }

  /// 停用／啟用。PATCH 每次修改都要**新的** client_request_id（同值代表同一次修改）。
  void _toggleActive(Routine r) {
    // TODO: 後端上線後改為
    //   api.updateRoutine(r.routineId, clientRequestId: _uuid.v4(), fields: {'active': !r.active})
    final i = _routines.indexWhere((e) => e.routineId == r.routineId);
    if (i < 0) return;
    setState(() {
      _routines[i] = Routine(
        routineId: r.routineId,
        elderId: r.elderId,
        title: r.title,
        type: r.type,
        schedule: r.schedule,
        remind: r.remind,
        createdBy: r.createdBy,
        active: !r.active,
        createdAt: r.createdAt,
      );
    });
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
              color: selected ? AppColors.accentText : AppColors.border,
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

  /// 送往 `POST /routines` 的欄位（`client_request_id` 由呼叫端另外帶）。
  Map<String, dynamic> toJson() => {
        'elder_id': AppSession.instance.selectedElderId,
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
              color: selected ? AppColors.accentText : AppColors.border,
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
