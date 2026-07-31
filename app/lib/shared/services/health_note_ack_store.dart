import 'package:shared_preferences/shared_preferences.dart';

import '../models/elder.dart';

/// 「哪幾筆 AI 記的健康註記，照護者已經確認過了」。
///
/// AI 是在照護者不在場時把東西寫進 `health_notes` 的（對話中的 `update_elder_profile`），
/// 所以那幾筆等於一份待辦：有人得看過並決定留或刪。沒被確認過的在畫面上標「新」。
///
/// **只存本機，這是刻意的選擇。** 「已確認」的語意是「**我**看過了」而不是
/// 「有人看過了」——一位長輩可以綁多位家人（api.md 的 caregiver_ids），A 看過不代表
/// B 不用看。所以這份狀態本來就該 per-caregiver，存在各自的裝置上與那個語意相符，
/// 不需要後端欄位。
///
/// 代價是換裝置或重裝 App 會全部變回「新」。那只是多看一次，可以接受；
/// 真要跨裝置一致，後端得做 per-caregiver 的確認欄位，成本遠高於它解決的問題。
class HealthNoteAckStore {
  HealthNoteAckStore._();
  static final HealthNoteAckStore instance = HealthNoteAckStore._();

  /// 依長輩分開存：確認狀態本來就是 per-elder，混在一起換長輩會看到別人的。
  static String _key(String elderId) => 'health_note_ack_$elderId';

  /// 已確認的 note_id。
  ///
  /// 順便把已經不存在的 note_id 清掉（那幾筆被刪了）——不清的話這份清單只會
  /// 單向長大，而且 note_id 一旦被後端重用就會誤判成已確認。
  Future<Set<String>> acked(
    String elderId, {
    required List<HealthNote> current,
  }) async {
    final prefs = await SharedPreferences.getInstance();
    final stored = prefs.getStringList(_key(elderId))?.toSet() ?? <String>{};

    final alive = current.map((n) => n.noteId).toSet();
    final pruned = stored.intersection(alive);
    if (pruned.length != stored.length) {
      await prefs.setStringList(_key(elderId), pruned.toList());
    }
    return pruned;
  }

  /// 把一筆標成已確認。
  Future<void> ack(String elderId, String noteId) async {
    final prefs = await SharedPreferences.getInstance();
    final stored = prefs.getStringList(_key(elderId))?.toSet() ?? <String>{};
    if (stored.add(noteId)) {
      await prefs.setStringList(_key(elderId), stored.toList());
    }
  }

  /// 測試與換帳號時清空。
  Future<void> clear(String elderId) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_key(elderId));
  }
}
