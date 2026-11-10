"""Angular curve resolution: 360 one-degree values to 180 two-degree points and back.

Edge sends the "Seimbang" curve as 180 points, each the average of degrees 2k and 2k+1. A consumer that
needs 360 values (map lobe, polar plot) rebuilds them with a periodic monotone cubic (PCHIP): smooth,
359 joins 0, and no overshoot, so it never invents a peak that was not in the points."""

def average_pairs(values: list[float]) -> list[float]:
    # 0.01 is the resolution KrakenSDR writes; the LoRa bridge packs values on that grid losslessly.
    return [round((values[2*k]+values[2*k+1])/2,2) for k in range(len(values)//2)]

def upsample_pairs(values: list[float]) -> list[float]:
    n=len(values)
    secant=[values[(k+1)%n]-values[k] for k in range(n)]  # per 2 degrees, from point k to k+1
    slope=[]
    for k in range(n):
        a,b=secant[k-1],secant[k]
        # Fritsch-Carlson: flat at a local extremum, else the harmonic mean of the two secants.
        slope.append(0.0 if a*b<=0 else 2*a*b/(a+b))
    out=[]
    for d in range(360):
        # Point k sits at degree 2k+0.5; degree d lies between point k and k+1 (mod n).
        x=(d-0.5)/2.0
        k=int(x//1); t=x-k; k%=n; j=(k+1)%n
        h00=2*t**3-3*t**2+1; h10=t**3-2*t**2+t; h01=-2*t**3+3*t**2; h11=t**3-t**2
        out.append(h00*values[k]+h10*slope[k]+h01*values[j]+h11*slope[j])
    return out
