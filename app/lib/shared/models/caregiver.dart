/// 綁定照護者的結果：綁到的是誰，以及這次是不是新綁的。
///
/// api.md 用 201／200 區分「這次才綁上」與「早就綁過了」，兩者 body 相同。
/// 對長輩來說這是兩句不同的話（「連結成功」vs「這位家人已經連結過了」），
/// 所以那一位元不能在解析 response 時被丟掉。
typedef CaregiverLink = ({Caregiver caregiver, bool isNew});

/// 照護者的對外身分。欄位規格見 docs/api.md。
///
/// 同一組欄位服務三個端點：`GET /me`（自己的 ID）、
/// `POST /elders/{id}/caregivers`（綁定結果）、`GET /elders/{id}/caregivers`（已綁定的家人），
/// 差別只在有沒有 [linkedAt]，所以不分成兩個型別。
///
/// 這裡**永遠不會有 Cognito `sub`**：sub 是 36 字 UUID，抄不動也念不清，後端以它穩定
/// 衍生 [caregiverId] 對外，sub 本身不出現在任何 response（見 docs/pii.md）。
class Caregiver {
  const Caregiver({
    required this.caregiverId,
    required this.name,
    this.linkedAt,
    this.isSelf = false,
  });

  /// `cg_` 後接 8 個小寫十六進位字元。同一個帳號永遠是同一組，不會換，
  /// 所以可以請長輩留著、之後重新綁定也用同一組。
  final String caregiverId;

  /// 顯示名稱。後端保證有值（Cognito `name`，未設定時取信箱 `@` 之前的部分）；
  /// 不放完整信箱（PII 最小化）。
  final String name;

  /// 首次綁定到某位長者的時間。`GET /me` 沒有這個欄位，所以可為 null。
  final DateTime? linkedAt;

  /// 這一筆就是呼叫者本人（見 docs/api.md 的 `is_self`）。
  ///
  /// 自我註冊的長輩會在自己的「已連結家人」清單裡看到自己，而且沒有名字——
  /// 建立長者資料時後端會把建立者的 sub 寫進 `caregiver_ids`，那時他還沒有
  /// `elder_id` claim。App 認不出那是誰（`GET /me` 是照護者專屬端點），所以由
  /// 後端標。
  final bool isSelf;

  factory Caregiver.fromJson(Map<String, dynamic> json) => Caregiver(
        caregiverId: json['caregiver_id'] as String? ?? '',
        name: json['name'] as String? ?? '',
        linkedAt: json['linked_at'] == null
            ? null
            : DateTime.tryParse(json['linked_at'] as String)?.toLocal(),
        isSelf: json['is_self'] as bool? ?? false,
      );
}
