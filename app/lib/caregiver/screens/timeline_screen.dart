import 'package:flutter/material.dart';

import '../../shared/models/life_event.dart';
import '../../shared/models/api_page.dart';
import '../../shared/services/care_repository.dart';
import '../../shared/services/session_store.dart';
import '../../shared/widgets/app_card.dart';
import '../../shared/widgets/async_view.dart';
import '../../shared/widgets/care_header.dart';
import '../../shared/widgets/status_chip.dart';
import '../../theme/app_theme.dart';

/// S6 `/care/timeline` — 生活事件時間軸。
///
/// `GET /events`：七類過濾、`next_token` 分頁（游標是不透明字串，原樣帶回）。
///
/// 這個畫面刻意在頁尾說明資料可見時機：例行公事完成與高風險事件在對話當下就查得到，
/// 一般生活事件要等 session 關閉且批次整理完才會出現（api.md hybrid 處理）。
/// 沒寫的話照護者會以為「剛剛講的怎麼沒進來」是壞掉了。
class TimelineScreen extends StatefulWidget {
  const TimelineScreen({super.key});

  @override
  State<TimelineScreen> createState() => _TimelineScreenState();
}

class _TimelineScreenState extends State<TimelineScreen> {
  late Future<ApiPage<LifeEvent>> _future;

  /// 已載入的所有頁；「載入更多」往後接。
  final _items = <LifeEvent>[];
  String? _nextToken;
  bool _loadingMore = false;

  /// null = 全部；否則只顯示該類。
  EventCategory? _filter;

  @override
  void initState() {
    super.initState();
    _load();
  }

  void _load() {
    _items.clear();
    _nextToken = null;
    _future = _fetchFirstPage();
  }

  Future<ApiPage<LifeEvent>> _fetchFirstPage() async {
    await AppSession.instance.ensureEldersLoaded();
    final elderId = AppSession.instance.selectedElderId;
    // 還沒綁定任何長輩就沒有事件可查。原本是 `selectedElderId!`，null 時丟
    // Null check operator，整頁變成「載入失敗」而重試永遠不會成功。
    // 詳見 stats_screen 的同名說明。
    if (elderId == null) {
      _items.clear();
      _nextToken = null;
      return const ApiPage(items: []);
    }
    // `EventCategory` 的 name 與 api.md 的 `type` 字串一一對應，可直接當參數送。
    final page = await CareRepo.instance.events(
      elderId: elderId,
      type: _filter?.name,
    );
    _items
      ..clear()
      ..addAll(page.items);
    _nextToken = page.nextToken;
    return page;
  }

  void _reload() => setState(_load);

  Future<void> _loadMore() async {
    final token = _nextToken;
    final elderId = AppSession.instance.selectedElderId;
    // elderId 理論上不會是 null（有游標就代表第一頁載成功過），但這裡不用 `!`：
    // 這一頁其餘地方就是被那個寫法炸掉的。
    if (token == null || elderId == null || _loadingMore) return;
    setState(() => _loadingMore = true);
    try {
      // 游標原樣帶回，不解析內容；查詢條件必須與取得游標時完全一致（api.md 共通分頁規則）
      final page = await CareRepo.instance.events(
        elderId: elderId,
        type: _filter?.name,
        nextToken: token,
      );
      if (!mounted) return;
      setState(() {
        _items.addAll(page.items);
        _nextToken = page.nextToken;
      });
    } finally {
      if (mounted) setState(() => _loadingMore = false);
    }
  }

  /// 換分類要整份重拉，不能只在本地篩已載入的那幾頁。
  ///
  /// 兩個理由：資料會長大，本地篩只會篩出「已載入範圍內的那一類」，看起來像少了紀錄；
  /// 而且 `next_token` 綁著取得它的那組查詢條件，換了 `type` 還沿用舊游標是錯的。
  void _changeFilter(EventCategory? c) {
    if (_filter == c) return;
    setState(() {
      _filter = c;
      _load();
    });
  }

  /// 本地再篩一次：後端已依 `type` 過濾，這裡是為了未接後端時（demo 資料不吃 `type`）
  /// 分類按鈕仍然有作用。兩邊同時成立，不會互相扣掉資料。
  List<LifeEvent> get _visible => _filter == null
      ? _items
      : _items.where((e) => EventCategory.fromType(e.type) == _filter).toList();

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.app,
      body: SafeArea(
        child: Column(
          children: [
            CareHeader(
              title: '生活時間軸',
              subtitle: '從對話中整理出來的生活紀錄',
              onElderChanged: (_) => _reload(),
            ),
            _FilterBar(
              selected: _filter,
              onChanged: _changeFilter,
            ),
            Expanded(
              child: AsyncView<ApiPage<LifeEvent>>(
                future: _future,
                onRetry: _reload,
                isEmpty: (_) => _items.isEmpty,
                emptyIcon: Icons.timeline_outlined,
                emptyText: '還沒有生活紀錄\n長輩開始對話後就會出現在這裡',
                builder: (context, _) {
                  final events = _visible;
                  if (events.isEmpty) {
                    return _NoMatch(onClear: () => _changeFilter(null));
                  }
                  return ListView.builder(
                    padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
                    // +1 給頁尾（載入更多／到底說明）
                    itemCount: events.length + 1,
                    itemBuilder: (context, i) {
                      if (i == events.length) return _buildFooter();
                      final e = events[i];
                      final prev = i == 0 ? null : events[i - 1];
                      return _EventTile(
                        key: ValueKey(e.eventId),
                        event: e,
                        // 同一天只在第一筆顯示日期分隔
                        showDate: prev == null || !_sameDay(prev.ts, e.ts),
                        isLast: i == events.length - 1,
                      );
                    },
                  );
                },
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildFooter() {
    final text = Theme.of(context).textTheme;
    return Padding(
      padding: const EdgeInsets.only(top: AppSpacing.sm),
      child: Column(
        children: [
          if (_nextToken != null)
            SizedBox(
              width: double.infinity,
              height: 48,
              child: OutlinedButton(
                onPressed: _loadingMore ? null : _loadMore,
                style: OutlinedButton.styleFrom(
                  foregroundColor: AppColors.ink,
                  side: const BorderSide(color: AppColors.border),
                  shape: const RoundedRectangleBorder(
                    borderRadius: BorderRadius.all(AppRadius.field),
                  ),
                ),
                child: _loadingMore
                    ? const SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(
                            strokeWidth: 2, color: AppColors.accent),
                      )
                    : Text('載入更早的紀錄', style: text.labelMedium),
              ),
            ),
          const SizedBox(height: AppSpacing.lg),
          // 資料可見時機說明（api.md hybrid 處理）
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Icon(Icons.info_outline,
                  size: 16, color: AppColors.chevron),
              const SizedBox(width: AppSpacing.sm),
              Expanded(
                child: Text(
                  '剛結束的對話需要一點時間整理，稍後才會出現在這裡。'
                  '用藥與需要注意的狀況則會立即記錄。',
                  style:
                      text.bodySmall?.copyWith(color: AppColors.inkSecondary),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  static bool _sameDay(DateTime a, DateTime b) =>
      a.year == b.year && a.month == b.month && a.day == b.day;
}

/// 分類過濾列的 key。事件卡上也有同名的分類膠囊，要靠這個指名過濾列裡的那顆。
const filterBarKey = ValueKey('timeline-filter-bar');

class _FilterBar extends StatelessWidget {
  const _FilterBar({required this.selected, required this.onChanged});

  final EventCategory? selected;
  final ValueChanged<EventCategory?> onChanged;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    // 換行而不是橫向捲動：分類滿七類之後，一屏只放得下五顆，剩下的躲在畫面外，
    // 而橫向捲動沒有任何視覺提示，等於那兩類不存在。換行雖然多吃一列高度，
    // 但七類全部看得到，兩倍字級下也只是再多換幾行。
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 4, 16, AppSpacing.sm),
      child: Wrap(
        key: filterBarKey,
        spacing: AppSpacing.sm,
        runSpacing: AppSpacing.sm,
        crossAxisAlignment: WrapCrossAlignment.center,
        children: [
          // 「全部」不屬於七類，另外畫
          Semantics(
            button: true,
            selected: selected == null,
            child: InkWell(
              onTap: () => onChanged(null),
              borderRadius: const BorderRadius.all(AppRadius.pill),
              child: Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
                decoration: BoxDecoration(
                  color:
                      selected == null ? AppColors.barDark : Colors.transparent,
                  borderRadius: const BorderRadius.all(AppRadius.pill),
                  border: Border.all(
                    color: selected == null
                        ? AppColors.barDark
                        : AppColors.borderInteractive,
                  ),
                ),
                child: Text('全部',
                    style: text.labelSmall?.copyWith(
                      color: selected == null
                          ? AppColors.onDark
                          : AppColors.inkSecondary,
                    )),
              ),
            ),
          ),
          for (final c in EventCategory.values)
            EventTypeChip(
              c,
              selected: selected == c,
              onTap: () => onChanged(selected == c ? null : c),
            ),
        ],
      ),
    );
  }
}

class _NoMatch extends StatelessWidget {
  const _NoMatch({required this.onClear});

  final VoidCallback onClear;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Icon(Icons.filter_alt_off_outlined,
              size: 40, color: AppColors.chevron),
          const SizedBox(height: AppSpacing.md),
          Text('這個分類目前沒有紀錄',
              style: text.bodyLarge?.copyWith(color: AppColors.inkSecondary)),
          const SizedBox(height: AppSpacing.md),
          TextButton(
            onPressed: onClear,
            child: Text('看全部',
                style: text.labelMedium?.copyWith(color: AppColors.accentText)),
          ),
        ],
      ),
    );
  }
}

/// 時間軸單筆：左欄時間與圓點（含連接線），右欄內容卡。
class _EventTile extends StatelessWidget {
  const _EventTile({
    super.key,
    required this.event,
    required this.showDate,
    required this.isLast,
  });

  final LifeEvent event;
  final bool showDate;
  final bool isLast;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final category = EventCategory.fromType(event.type);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (showDate)
          Padding(
            padding: const EdgeInsets.only(
                top: AppSpacing.sm, bottom: AppSpacing.sm),
            child: Text(_dayLabel(event.ts),
                style: text.labelMedium?.copyWith(
                    color: AppColors.inkSecondary, letterSpacing: 1.5)),
          ),
        IntrinsicHeight(
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              SizedBox(
                width: 48,
                child: Text(_timeLabel(event.ts),
                    style: text.bodySmall
                        ?.copyWith(color: AppColors.inkSecondary)),
              ),
              // 圓點 + 垂直連接線
              Column(
                children: [
                  Padding(
                    padding: const EdgeInsets.only(top: 2),
                    child: EventDot(category),
                  ),
                  if (!isLast)
                    Expanded(
                      child: Container(
                        width: 2,
                        margin: const EdgeInsets.symmetric(vertical: 4),
                        color: AppColors.border,
                      ),
                    ),
                ],
              ),
              const SizedBox(width: AppSpacing.md),
              Expanded(
                child: Padding(
                  padding: const EdgeInsets.only(bottom: AppSpacing.md),
                  child: AppCard(
                    padding: const EdgeInsets.all(AppSpacing.md),
                    semanticLabel:
                        '${_timeLabel(event.ts)} ${category.label}：${event.detail}',
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: [
                            EventTypeChip(category),
                            const Spacer(),
                            // 手動記錄 vs 對話擷取：來源要看得出來
                            if (event.source == 'manual')
                              Text('手動記錄',
                                  style: text.bodySmall
                                      ?.copyWith(color: AppColors.chevron)),
                          ],
                        ),
                        const SizedBox(height: AppSpacing.sm),
                        Text(event.detail, style: text.bodyMedium),
                        if (event.routineId != null) ...[
                          const SizedBox(height: AppSpacing.sm),
                          Row(
                            children: [
                              const Icon(Icons.link,
                                  size: 14, color: AppColors.chevron),
                              const SizedBox(width: 4),
                              Text('對應例行公事',
                                  style: text.bodySmall?.copyWith(
                                      color: AppColors.inkSecondary)),
                            ],
                          ),
                        ],
                      ],
                    ),
                  ),
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }

  static String _timeLabel(DateTime t) =>
      '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';

  static String _dayLabel(DateTime t) {
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    final d = DateTime(t.year, t.month, t.day);
    final diff = today.difference(d).inDays;
    if (diff == 0) return '今天';
    if (diff == 1) return '昨天';
    return '${t.month} 月 ${t.day} 日';
  }
}
