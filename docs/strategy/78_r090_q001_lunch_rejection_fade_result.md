# TASK-R090-Q001 結果: 現物昼休み変位の再開否定後fade

実行日: **2026-09-15 JST**  
最終run: `r090-q001-20260915-lunch-rejection-fade-02`  
判定: **INCONCLUSIVE（PnL未取得）**

事前登録[77_r090_q001_lunch_rejection_fade.md](77_r090_q001_lunch_rejection_fade.md)を凍結し、Development（trade_date 2021-01-01--2025-06-30）の正規化Parquetだけを読み取った。OOS、Walk Forward、Final Holdoutは未アクセスである。これはR089の救済ではなく、現物昼休み中の先物価格発見と12:30再開後の否定を検査する別機序として登録した。

PnL前監査では、p0=11:30 open、p1=12:29 close、p2=12:39 close、`J=-sign(D)*(p2-p1)/abs(D)`、D=0・必要区間欠損・R004隔離、current除外の直前160予定日/140有効日、current閾値帯で再分類した過去J、翌適格bar entry、14:55 exit、および午前placebo（10:00/10:59/11:09、11:10--13:25）を確認した。因果性監査とB1/B2のLR/LA M共通支持はPASSし、説明不能除外は0件だった。

S2のLR/LA/PRはそれぞれ106/109/134件、LRの上昇/下落は61/45件、B1はLR/LA=55/55、B2=51/54だった。LRは2022/2023/2024で25/35/36件と各下限を満たした一方、2025H1は**7件**で事前固定下限8件を1件下回った。従ってS2のANDは不成立であり、指定どおりreturn、PnL、PF、bootstrap、orders/fills/trades、感度、年別損益、OOS、Walk Forward、Final Holdoutを取得せず停止した。

`...-01`はPnL-free監査で同値LR/LAの理由コードを説明不能として扱った技術記録である。PnLへのアクセス前に分類のみを修正し、新IDで`...-02`を再登録した。件数下限は修正後も不成立である。最終成果物は`results/research/r090-q001-20260915-lunch-rejection-fade-02/`に保存した。
