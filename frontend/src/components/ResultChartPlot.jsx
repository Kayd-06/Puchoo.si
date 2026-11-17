import {
  ResponsiveContainer,
  BarChart,
  Bar,
  LineChart,
  Line,
  AreaChart,
  Area,
  ScatterChart,
  Scatter,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  RadarChart,
  Radar,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  Treemap,
  ComposedChart,
  Brush,
} from 'recharts';
// Theme-aware data colors also resolve to concrete colors in SVG downloads.
export const chartColors = [
  'var(--chart-series-1, #0d9488)',
  'var(--chart-series-2, #7c3aed)',
  'var(--chart-series-3, #d97706)',
  'var(--chart-series-4, #e11d48)',
  'var(--chart-series-5, #2563eb)',
  'var(--chart-series-6, #16a34a)',
  'var(--chart-series-7, #db2777)',
  'var(--chart-series-8, #ea580c)',
];
export const chartColorNames = [
  'Teal',
  'Violet',
  'Amber',
  'Rose',
  'Blue',
  'Green',
  'Pink',
  'Orange',
];
export const formatChartNumber = (value) =>
  value == null
    ? 'Missing'
    : new Intl.NumberFormat(
        undefined,
        Math.abs(value) > 0 && Math.abs(value) < 0.01
          ? { maximumSignificantDigits: 4 }
          : { maximumFractionDigits: 2 },
      ).format(value);
export const chartNames = {
  bar: 'Vertical bars',
  horizontal: 'Horizontal bars',
  line: 'Line',
  step: 'Step line',
  area: 'Area',
  scatter: 'Scatter',
  pie: 'Pie',
  donut: 'Donut',
  radar: 'Radar',
  treemap: 'Treemap',
  grouped: 'Grouped bars',
  stacked: 'Stacked bars',
  combo: 'Bar + line',
};
export const comparisonTypes = ['grouped', 'stacked', 'combo'];

export default function ResultChartPlot({
  data,
  type,
  xLabel,
  yLabel,
  secondaryLabel,
  colors = chartColors,
}) {
  const comparison = comparisonTypes.includes(type);
  const common = {
    data,
    margin: { top: 20, right: type === 'combo' ? 20 : 28, bottom: 20, left: 8 },
    accessibilityLayer: true,
  };
  const tooltip = (
    <Tooltip
      contentStyle={{
        background: 'var(--report-surface, #fff)',
        color: 'var(--report-text, #262626)',
        border: '1px solid var(--report-line, #d4d4d4)',
        borderRadius: 8,
      }}
      formatter={(value, name) => [formatChartNumber(value), name]}
    />
  );
  const horizontal = type === 'horizontal';
  const axes = (
    <>
      <CartesianGrid strokeDasharray="3 3" stroke="var(--report-line, #888)" vertical={false} />
      <XAxis
        dataKey={horizontal ? undefined : type === 'scatter' ? 'x' : 'label'}
        type={horizontal || type === 'scatter' ? 'number' : 'category'}
        name={horizontal ? yLabel : xLabel}
        tick={{ fill: 'currentColor', fontSize: 12 }}
        tickFormatter={horizontal ? formatChartNumber : undefined}
        minTickGap={24}
      />
      <YAxis
        dataKey={horizontal ? 'label' : type === 'scatter' ? 'value' : undefined}
        type={horizontal ? 'category' : 'number'}
        name={horizontal ? xLabel : yLabel}
        tickFormatter={horizontal ? undefined : formatChartNumber}
        tick={{ fill: 'currentColor', fontSize: 12 }}
        width={horizontal ? 110 : 80}
      />
      {type === 'combo' && (
        <YAxis
          yAxisId="secondary"
          orientation="right"
          tickFormatter={formatChartNumber}
          tick={{ fill: colors[1], fontSize: 12 }}
          width={80}
        />
      )}
      {tooltip}
      {data.length > 30 && !horizontal && type !== 'scatter' && (
        <Brush dataKey="label" height={24} stroke={colors[0]} />
      )}
    </>
  );
  let chart;
  if (type === 'pie' || type === 'donut')
    chart = (
      <PieChart {...common}>
        <Pie
          data={data}
          dataKey="value"
          nameKey="label"
          innerRadius={type === 'donut' ? '52%' : 0}
          outerRadius="80%"
          paddingAngle={2}
          isAnimationActive={false}
        >
          {data.map((p, i) => (
            <Cell key={p.label} fill={colors[i % colors.length]} />
          ))}
        </Pie>
        {tooltip}
      </PieChart>
    );
  else if (type === 'radar')
    chart = (
      <RadarChart {...common} outerRadius="75%">
        <PolarGrid />
        <PolarAngleAxis dataKey="label" tick={{ fill: 'currentColor', fontSize: 12 }} />
        <PolarRadiusAxis tick={{ fill: 'currentColor', fontSize: 11 }} />
        <Radar
          name={yLabel}
          dataKey="value"
          stroke={colors[0]}
          fill={colors[0]}
          fillOpacity={0.25}
          isAnimationActive={false}
        />
        {tooltip}
      </RadarChart>
    );
  else if (type === 'treemap')
    chart = (
      <Treemap
        data={data.filter((p) => p.value > 0)}
        dataKey="value"
        nameKey="label"
        stroke="var(--report-surface, #fff)"
        fill={colors[0]}
        isAnimationActive={false}
      >
        {tooltip}
      </Treemap>
    );
  else if (type === 'line' || type === 'step')
    chart = (
      <LineChart {...common}>
        {axes}
        <Line
          name={yLabel}
          type={type === 'step' ? 'stepAfter' : 'linear'}
          dataKey="value"
          stroke={colors[0]}
          strokeWidth={2.5}
          dot={data.length < 40}
          isAnimationActive={false}
        />
      </LineChart>
    );
  else if (type === 'area')
    chart = (
      <AreaChart {...common}>
        {axes}
        <Area
          name={yLabel}
          type="linear"
          dataKey="value"
          stroke={colors[0]}
          fill={colors[0]}
          fillOpacity={0.2}
          isAnimationActive={false}
        />
      </AreaChart>
    );
  else if (type === 'scatter')
    chart = (
      <ScatterChart {...common}>
        {axes}
        <Scatter
          data={data.filter((p) => p.value !== null)}
          name={yLabel}
          fill={colors[0]}
          isAnimationActive={false}
        />
      </ScatterChart>
    );
  else if (type === 'combo')
    chart = (
      <ComposedChart {...common}>
        {axes}
        <Bar
          dataKey="value"
          name={yLabel}
          fill={colors[0]}
          radius={[4, 4, 0, 0]}
          isAnimationActive={false}
        />
        <Line
          yAxisId="secondary"
          dataKey="secondary"
          name={secondaryLabel}
          stroke={colors[1]}
          strokeWidth={2.5}
          isAnimationActive={false}
        />
      </ComposedChart>
    );
  else
    chart = (
      <BarChart {...common} layout={horizontal ? 'vertical' : 'horizontal'}>
        {axes}
        <Bar
          dataKey="value"
          name={yLabel}
          stackId={type === 'stacked' ? 'metrics' : undefined}
          fill={colors[0]}
          radius={type === 'stacked' ? 0 : horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0]}
          isAnimationActive={false}
        />
        {comparison && (
          <Bar
            dataKey="secondary"
            name={secondaryLabel}
            stackId={type === 'stacked' ? 'metrics' : undefined}
            fill={colors[1]}
            isAnimationActive={false}
          />
        )}
      </BarChart>
    );
  return (
    <ResponsiveContainer width="100%" height="100%">
      {chart}
    </ResponsiveContainer>
  );
}
