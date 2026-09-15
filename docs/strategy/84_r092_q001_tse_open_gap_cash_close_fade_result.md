# R092-Q001: 前回TSE通常session終値から09:00始値への大幅gapのcash-close fade — INCONCLUSIVE

`TASK-R092-Q001` の正本成果物は [task-r092-q001-tse-open-gap-cash-close-fade-20260915-02](../../results/research/task-r092-q001-tse-open-gap-cash-close-fade-20260915-02/) に保存した。`…-01`は、無効観測にもp/o/g/x列を要求してR057定義再利用監査をfalseにしたPnL-free監査述語の技術不備で停止した。不備はPnL、注文、fill、trade、return、bootstrap、判定の前に検出され、`…-02`ではその監査述語だけを修正し、同じ凍結仕様を再実行した。

PnL前にR001--R091との重複、R006/R057/R091の既知REJECT、R057と同じgap familyへの事後追加仮説、Development反復利用、225Labo連続系列限定を記録した。R057本体は変更せず、p=直前予定TSE通常session最終1分足close、o=09:00予定足open、g=(o-p)/p、x=abs(g)、直前120予定TSE営業日の補充なし・有効100以上・nearest-rank q75・等号上側を再利用した。

予定軸はDevelopmentの公式TSE営業日1,099 `trade_date`。R004 whole-session隔離、trade_date、submit/decision/fill、公式T（旧15:00、新15:30）、current-excluded参照、entryを将来exit可用性で取消さないこと、q70感度対象にも完了exitがあることの因果性監査はすべてPASSした。説明不能除外0、entry後未完了0、A/B/C/D共通のprimary E_exec/完了pathは240件、gap-up/downは124/116、旧/新制度は206/34、2025H1は32件で、これらのPnL前条件は通過した。

ただし年別のprimary complete pathは2021=17、2022=65、2023=60、2024=66、2025H1=32であり、「2021--2024各年30件以上」の固定gateだけが未達だった。よって **R092-Q001=INCONCLUSIVE**。価格return、PnL、PF、bootstrap、q70/q80、entry/exit・費用感度、top-10除外を取得していない。結果依存の閾値・時刻・exit・片側gap変更、Walk Forward、2025H2 OOS、Final Holdoutには進まない。
