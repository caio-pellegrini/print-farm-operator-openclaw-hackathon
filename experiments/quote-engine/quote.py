#!/usr/bin/env python3
"""Transparent cost/price calculator. Inputs are example defaults, not market facts."""
import argparse
import json


def quote(weight_g, hours, material_brl_kg=90, machine_brl_h=2, energy_kwh=0.12,
          energy_brl_kwh=0.95, margin=0.40, quantity=1, setup_brl=0,
          minimum_order_fee=0, currency="BRL"):
    material = weight_g * quantity / 1000 * material_brl_kg
    machine = hours * quantity * machine_brl_h
    energy = hours * quantity * energy_kwh * energy_brl_kwh
    cost = material + machine + energy + setup_brl
    price = max(cost / (1 - margin), minimum_order_fee)
    return {"inputs": locals(), "estimated_cost_brl": round(cost, 2),
            "suggested_unit_price_brl": round(price / quantity, 2),
            "suggested_order_price_brl": round(price, 2),
            "estimated_gross_profit_brl": round(price - cost, 2), "currency": currency}


def quote_slicer_result(result, *, material_brl_kg=90, machine_brl_h=2,
                        material_density_g_cm3=1.24, energy_kwh=0.12,
                        energy_brl_kwh=0.95, margin=0.40,
                        quantity=1, setup_brl=0):
    """Quote from the common adapter result without branching on slicer ID."""
    material = result["material_consumption"]
    volume_mm3 = material["volume_mm3"]
    if not 0 < material_density_g_cm3 < 10:
        raise ValueError("Material density must be a positive configured value in g/cm³.")
    grams = volume_mm3 / 1000 * material_density_g_cm3
    seconds = result["print_time_seconds"]
    if grams <= 0 or seconds <= 0:
        raise ValueError("A slicer result must include positive filament volume and print time.")
    result = quote(
        grams,
        seconds / 3600,
        material_brl_kg=material_brl_kg,
        machine_brl_h=machine_brl_h,
        energy_kwh=energy_kwh,
        energy_brl_kwh=energy_brl_kwh,
        margin=margin,
        quantity=quantity,
        setup_brl=setup_brl,
    )
    result["inputs"]["filament_volume_mm3"] = volume_mm3
    result["inputs"]["material_density_g_cm3"] = material_density_g_cm3
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--weight-g", type=float, required=True)
    p.add_argument("--hours", type=float, required=True)
    p.add_argument("--material-brl-kg", type=float, default=90)
    p.add_argument("--machine-brl-h", type=float, default=2)
    p.add_argument("--energy-kwh", type=float, default=.12)
    p.add_argument("--energy-brl-kwh", type=float, default=.95)
    p.add_argument("--margin", type=float, default=.40)
    p.add_argument("--quantity", type=int, default=1)
    p.add_argument("--setup-brl", type=float, default=0)
    p.add_argument("--minimum-order-fee", type=float, default=0)
    p.add_argument("--currency", default="BRL")
    a = p.parse_args()
    if a.quantity <= 0 or not 0 <= a.margin < 1:
        p.error("quantity must be positive and margin in [0, 1)")
    args = vars(a)
    print(json.dumps(quote(args.pop("weight_g"), args.pop("hours"), **args), indent=2))


if __name__ == "__main__":
    main()
