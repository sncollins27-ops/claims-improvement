import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart, LineChart, HeatmapChart, ScatterChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent, VisualMapComponent, TitleComponent } from 'echarts/components'
import VChart from 'vue-echarts'

use([CanvasRenderer, BarChart, LineChart, HeatmapChart, ScatterChart, GridComponent, TooltipComponent, LegendComponent, VisualMapComponent, TitleComponent])

export { VChart }

export const chartTheme = {
  textStyle: { color: '#8b98b8', fontFamily: 'Inter' },
  axisLine: { lineStyle: { color: '#2a3554' } },
  splitLine: { lineStyle: { color: '#1c2540' } },
}

export function baseAxis(extra: Record<string, any> = {}) {
  return {
    axisLine: { lineStyle: { color: '#2a3554' } },
    axisTick: { show: false },
    axisLabel: { color: '#8b98b8', fontSize: 11 },
    splitLine: { lineStyle: { color: '#1c2540' } },
    ...extra,
  }
}

export const tooltipStyle = {
  backgroundColor: '#0f1523',
  borderColor: '#2a3554',
  textStyle: { color: '#e8ecf6', fontSize: 12 },
}

export const palette = ['#4f7cff', '#3ddc97', '#f5b84a', '#a78bfa', '#ff6b81', '#38bdf8']
