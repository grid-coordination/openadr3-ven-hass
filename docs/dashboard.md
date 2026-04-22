# Dashboard Setup

This guide shows how to build a dashboard with current-value cards and 72-hour forecast charts for price and GHG data.

## Prerequisites

Install these frontend cards via HACS (**HACS → Frontend → Search → Install**):

- [**Mushroom Cards**](https://github.com/piitaya/lovelace-mushroom) — clean entity cards for current values
- [**ApexCharts Card**](https://github.com/RomRider/apexcharts-card) — time-series charts for forecast data

Restart Home Assistant after installing.

## Dashboard Layout

The recommended layout uses a **Sections** view with two grid sections — one for electricity pricing and one for GHG emissions. Each section pairs a Mushroom entity card (current value at a glance) with an ApexCharts forecast chart.

### Creating the Dashboard

1. **Settings → Dashboards → Add Dashboard**
2. Give it a name (e.g. "Grid") and a URL path (e.g. `dashboard-ven`)
3. Choose **Sections** as the view type
4. Add two sections, each with the cards below

### Section 1: Electricity Price

#### Current Price (Mushroom Entity Card)

```yaml
type: custom:mushroom-entity-card
entity: sensor.openadr3_vtn_price_grid_coordination_energy_eelec_024131103
name: Price
icon: mdi:currency-usd
```

#### Price Forecast (ApexCharts Card)

```yaml
type: custom:apexcharts-card
header:
  title: Electricity Price Forecast (72h)
  show: true
graph_span: 72h
span:
  start: day
now:
  show: true
  label: Now
  color: red
series:
  - entity: sensor.openadr3_vtn_price_grid_coordination_energy_eelec_024131103
    name: Price
    data_generator: |
      const forecast = entity.attributes.forecast || [];
      return forecast.map((entry) => {
        return [new Date(entry.datetime).getTime(), entry.value];
      });
    type: area
    curve: stepline
    stroke_width: 2
    color: "#1976D2"
    opacity: 0.3
yaxis:
  - apex_config:
      title:
        text: "$/kWh"
apex_config:
  chart:
    height: 300
  tooltip:
    x:
      format: "ddd MMM dd HH:00"
  xaxis:
    type: datetime
```

### Section 2: GHG Emissions

#### Current Emissions (Mushroom Entity Card)

```yaml
type: custom:mushroom-entity-card
entity: sensor.openadr3_vtn_price_grid_coordination_energy_moer_pge
name: GHG
icon_color: green
```

#### Emissions Forecast (ApexCharts Card)

```yaml
type: custom:apexcharts-card
header:
  title: GHG Emissions Forecast (72h)
  show: true
graph_span: 72h
span:
  start: day
now:
  show: true
  label: Now
  color: red
series:
  - entity: sensor.openadr3_vtn_price_grid_coordination_energy_moer_pge
    name: MOER
    data_generator: |
      const forecast = entity.attributes.forecast || [];
      return forecast.map((entry) => {
        return [new Date(entry.datetime).getTime(), entry.value];
      });
    type: area
    curve: stepline
    stroke_width: 2
    color: "#2E7D32"
    opacity: 0.3
yaxis:
  - apex_config:
      title:
        text: "g CO₂/kWh"
apex_config:
  chart:
    height: 300
  tooltip:
    x:
      format: "ddd MMM dd HH:00"
  xaxis:
    type: datetime
```

## Adapting for Your Sensors

Replace the `entity` values with your actual sensor entity IDs. You can find them in **Settings → Devices & Services → OpenADR 3 VEN** → click the device → look at the entity IDs listed.

The `data_generator` works with any sensor created by this integration — it reads the `forecast` attribute which contains hourly data with `datetime` and `value` fields.

## Result

![Dashboard screenshot](../docs/dashboard-screenshot.png)

The dashboard shows:
- **Mushroom cards** with the current hour's price and emissions values
- **72-hour forecast charts** starting from the beginning of today
- A red **"Now"** marker showing the current time
- **Hourly step** values matching the VTN's event intervals
- Daily patterns clearly visible (e.g. solar dip in midday pricing)
