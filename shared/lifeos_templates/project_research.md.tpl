# __TITLE__

## 🎯 對應 OKR
- **季度計畫**：`= this.quarter`
- **關鍵結果**：`= this.parent_kr`

## ✅ Tasks

```base
filters:
  and:
    - file.hasTag("task")
    - file.inFolder("TaskNotes/Tasks")
    - projects.contains(link("__TITLE__"))
formulas:
  實際🍅: if(timeEntries, (list(timeEntries).filter(value.endTime).map((number(date(value.endTime)) - number(date(value.startTime))) / 60000).reduce(acc + value, 0) / 25).floor(), 0)
  accuracy: if("預估🍅" && "預估🍅" > 0 && formula.實際🍅 > 0, (formula.實際🍅 / "預估🍅" * 100).round(), null)
views:
  - type: table
    name: Active
    order:
      - ✅
      - file.name
      - priority
      - scheduled
      - 預估🍅
      - formula.實際🍅
    sort:
      - property: scheduled
        direction: ASC
```

## 📊 番茄統計

```dataviewjs
const name = dv.current().file.name;
const tasks = dv.pages('"TaskNotes/Tasks"').where(p =>
  p.projects && String(p.projects).includes(name)
);

const totalEst = tasks.values.reduce((s, p) => s + (Number(p["預估🍅"]) || 0), 0);

const totalTrackedMin = tasks.values.reduce((s, p) => {
  if (!p.timeEntries) return s;
  const entries = Array.from(p.timeEntries);
  return s + entries
    .filter(e => e.endTime)
    .reduce((sum, e) => sum + (new Date(String(e.endTime)) - new Date(String(e.startTime))) / 60000, 0);
}, 0);
const totalActual = Math.floor(totalTrackedMin / 25);

const accuracy = totalEst > 0 && totalActual > 0
  ? Math.round(totalActual / totalEst * 100) + "%"
  : "—";

const done = tasks.values.filter(p => p.status === "done" || p.status === "achieved").length;
const total = tasks.values.length;

dv.table(
  ["Tasks 完成", "預估🍅", "實際🍅", "預估準確率"],
  [[(done + " / " + total), totalEst, totalActual, accuracy]]
);
```

---

## 專案描述


## 預期成果


## Literature Notes



## Synthesis



## 專案筆記

