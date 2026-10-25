using System.Collections.Generic;
using UnityEngine;

namespace B36Peacemaker
{
    // Drives the B-36 exterior from the game's own (hidden) systems:
    //  - bomb doors follow the native bay doors the builder assigned to each bay,
    //  - landing gear follows the native gear legs,
    //  - each turret aims along the gun of the native turret weapon spawned at its hardpoint,
    //  - control surfaces, flaps, wheels and propellers follow the rigidbody.
    // The builder (unity/Assets/Editor/B36Peacemaker/B36Builder.cs) leaves name markers that
    // this class looks up; the two must agree on the names in Markers.
    public static class Markers
    {
        public const string RigFrame = "B36_RigFrame";
        public const string Model = "B36_Model";
        public const string SourcePrefix = "B36_Src_";   // child of a native transform
        public const string RestPrefix = "B36_Rest_";    // child of the rig frame, at that native transform's prefab pose
        public const string Door = "Door_";              // B36_Src_Door_<bay>_<n>
        public const string Gear = "Gear_";              // B36_Src_Gear_<n>
        public const string Turret = "Turret_";          // B36_Src_Turret_<turret name>, child of a hardpoint
    }

    public class B36Animator : MonoBehaviour
    {
        public RigData rig;

        const float PropRpm = 1150f;       // propeller rpm at cruise (2,700 engine rpm through 0.4375 gearing), visual
        const float ExtendTime = 4f;       // s, turret extension, from the source turrets.ts
        const float TurretSlew = 45f;      // deg/s, from the source turrets.ts
        const float TurretIdle = 8f;       // s without native gun movement before a turret stows

        readonly List<Hinge> surfaces = new List<Hinge>();
        readonly List<Hinge> flaps = new List<Hinge>();
        readonly List<Hinge> props = new List<Hinge>();
        readonly List<Hinge> wheels = new List<Hinge>();
        readonly List<Hinge> gear = new List<Hinge>();
        readonly Dictionary<int, List<Hinge>> doors = new Dictionary<int, List<Hinge>>();
        readonly Dictionary<int, List<NativeMotion>> doorSources = new Dictionary<int, List<NativeMotion>>();
        readonly List<NativeMotion> gearSources = new List<NativeMotion>();
        readonly List<TurretDriver> turrets = new List<TurretDriver>();

        Rigidbody body;
        float elevator, aileron, rudder, flap, propAngle, wheelAngle, gearDown = 1f, nextHide;

        void Start()
        {
            body = GetComponentInParent<Rigidbody>();
            var model = transform.Find(Markers.Model);
            if (model == null || rig == null)
            {
                Debug.LogError("[B36] Rig frame without model or rig data; animation disabled.");
                enabled = false;
                return;
            }
            var named = new Dictionary<string, Transform>();
            foreach (var t in model.GetComponentsInChildren<Transform>(true))
                if (!named.ContainsKey(t.name)) named.Add(t.name, t);

            foreach (var p in rig.parts)
            {
                Transform node;
                if (!named.TryGetValue(p.name, out node))
                {
                    Debug.LogWarning("[B36] Model has no " + p.name);
                    continue;
                }
                var hinge = new Hinge(transform, node, RigData.V(p.pivot), RigData.V(p.axis), p);
                switch (p.kind)
                {
                    case "aileron":
                    case "elevator":
                    case "rudder": surfaces.Add(hinge); break;
                    case "flap": flaps.Add(hinge); break;
                    case "prop": props.Add(hinge); break;
                    case "wheel": wheels.Add(hinge); break;
                    case "gear": gear.Add(hinge); break;
                    case "bombdoor":
                        if (!doors.ContainsKey(p.bay)) doors[p.bay] = new List<Hinge>();
                        doors[p.bay].Add(hinge);
                        break;
                }
            }

            var aircraft = transform.parent;
            foreach (var t in aircraft.GetComponentsInChildren<Transform>(true))
            {
                if (!t.name.StartsWith(Markers.SourcePrefix)) continue;
                var key = t.name.Substring(Markers.SourcePrefix.Length);
                var native = t.parent;
                if (key.StartsWith(Markers.Turret))
                {
                    RigTurret data = null;
                    foreach (var r in rig.turrets) if (r.name == key) data = r;
                    Transform yaw, guns;
                    if (data != null && named.TryGetValue(data.name, out yaw) && named.TryGetValue(data.guns, out guns))
                        turrets.Add(new TurretDriver(transform, native, t, yaw, guns, data));
                    continue;
                }
                var rest = transform.Find(Markers.RestPrefix + key);
                if (rest == null)
                {
                    Debug.LogWarning("[B36] No rest pose for " + key);
                    continue;
                }
                // Gear legs travel a long way when they fold; the floor keeps oleo movement
                // from reading as a retraction.
                var motion = new NativeMotion(native, rest, key.StartsWith(Markers.Gear) ? 0.6f : 0.15f);
                if (key.StartsWith(Markers.Door))
                {
                    var bay = ParseBay(key);
                    if (!doorSources.ContainsKey(bay)) doorSources[bay] = new List<NativeMotion>();
                    doorSources[bay].Add(motion);
                }
                else if (key.StartsWith(Markers.Gear))
                {
                    gearSources.Add(motion);
                }
            }
            Debug.Log("[B36] Animating " + name + ": " + surfaces.Count + " surfaces, " + doorSources.Count +
                      " bays, " + gearSources.Count + " gear legs, " + turrets.Count + " turrets.");
        }

        static int ParseBay(string key)
        {
            // Door_<bay>_<n>
            var parts = key.Split('_');
            int bay;
            return parts.Length > 1 && int.TryParse(parts[1], out bay) ? bay : 0;
        }

        void LateUpdate()
        {
            var dt = Time.deltaTime;
            var w = body != null ? transform.InverseTransformDirection(body.angularVelocity) : Vector3.zero;
            var v = body != null ? transform.InverseTransformDirection(body.velocity) : Vector3.zero;

            // Surfaces follow the body rates (Unity is left-handed: +x pitches the nose down,
            // +z rolls left, +y yaws right). Positive hinge angles are trailing edge down,
            // except the rudder, where positive is trailing edge right (source aircraft.ts).
            elevator = Mathf.Lerp(elevator, Mathf.Clamp(-w.x / 0.25f, -1f, 1f), dt * 6f);
            aileron = Mathf.Lerp(aileron, Mathf.Clamp(-w.z / 0.5f, -1f, 1f), dt * 6f);
            rudder = Mathf.Lerp(rudder, Mathf.Clamp(w.y / 0.15f, -1f, 1f), dt * 6f);
            foreach (var h in surfaces)
            {
                float a;
                if (h.part.kind == "elevator") a = -elevator;
                else if (h.part.kind == "rudder") a = rudder;
                else a = h.part.side == "R" ? -aileron : aileron;
                h.Set(a * h.part.angle);
            }

            gearDown = 1f;
            if (gearSources.Count > 0)
            {
                float up = 0f;
                foreach (var g in gearSources) up = Mathf.Max(up, g.Progress());
                gearDown = 1f - up;
            }
            // Hidden by deactivating rather than through renderers: the game switches the
            // exterior renderers off and on itself for the cockpit view.
            var showGear = gearDown > 0.02f;
            foreach (var h in gear)
            {
                h.Set((1f - gearDown) * h.part.angle);
                h.Show(showGear);
            }

            // Flaps half down in the circuit: gear down and slow.
            var wantFlap = gearDown > 0.5f && v.z < 95f ? 0.5f : 0f;
            flap = Mathf.MoveTowards(flap, wantFlap, dt * 0.1f);
            foreach (var h in flaps) h.Set(flap * h.part.angle);

            foreach (var pair in doors)
            {
                float open = 0f;
                List<NativeMotion> src;
                if (doorSources.TryGetValue(pair.Key, out src))
                    foreach (var s in src) open = Mathf.Max(open, s.Progress());
                foreach (var h in pair.Value) h.Set(open * h.part.angle);
            }

            propAngle = (propAngle + PropRpm / 60f * 360f * dt) % 360f;
            foreach (var h in props) h.Set(propAngle);

            var ground = gearDown > 0.99f ? v.z : 0f;
            wheelAngle = (wheelAngle - ground / 0.7f * Mathf.Rad2Deg * dt) % 360f;
            foreach (var h in wheels) h.Set(wheelAngle * 0.7f / Mathf.Max(h.part.radius, 0.1f));

            var hide = Time.time >= nextHide;
            if (hide) nextHide = Time.time + 1f;
            foreach (var t in turrets) t.Update(dt, hide);
        }

        // A model part turned about a fixed axis through a fixed pivot, both given in rig
        // (model) space and converted once into the part's parent space, so nested parts
        // (gun cradles in turrets, wheels on legs) follow their parents.
        class Hinge
        {
            public readonly RigPart part;
            readonly Transform node;
            readonly Vector3 restPos, pivot, axis;
            readonly Quaternion restRot;

            public Hinge(Transform rigFrame, Transform node, Vector3 rigPivot, Vector3 rigAxis, RigPart part)
            {
                this.part = part;
                this.node = node;
                restPos = node.localPosition;
                restRot = node.localRotation;
                var parent = node.parent;
                var toParent = parent.worldToLocalMatrix * rigFrame.localToWorldMatrix;
                pivot = toParent.MultiplyPoint3x4(rigPivot);
                // An axis keeps its rotation sense through a mirror only if it flips with it.
                var det = Det(toParent);
                axis = toParent.MultiplyVector(rigAxis).normalized * Mathf.Sign(det);
            }

            public void Set(float degrees)
            {
                var q = Quaternion.AngleAxis(degrees, axis);
                node.localRotation = q * restRot;
                node.localPosition = pivot + q * (restPos - pivot);
            }

            public void Show(bool show)
            {
                if (node.gameObject.activeSelf != show) node.gameObject.SetActive(show);
            }
        }

        static float Det(Matrix4x4 m)
        {
            return m.m00 * (m.m11 * m.m22 - m.m12 * m.m21)
                 - m.m01 * (m.m10 * m.m22 - m.m12 * m.m20)
                 + m.m02 * (m.m10 * m.m21 - m.m11 * m.m20);
        }

        // How far a hidden native part has moved from its prefab pose, as 0..1. The pose is
        // compared in world space against a rest marker that rides on the rig frame, so it
        // catches movement of the part or any of its parents. The scale is the largest
        // movement seen so far (at least MinAngle or minTravel), so it works for doors and
        // legs whichever way the game moves them.
        class NativeMotion
        {
            const float MinAngle = 20f;
            readonly Transform native, rest;
            float maxAngle = MinAngle, maxTravel;

            public NativeMotion(Transform native, Transform rest, float minTravel)
            {
                this.native = native;
                this.rest = rest;
                maxTravel = minTravel;
            }

            public float Progress()
            {
                if (native == null) return 0f;
                var angle = Quaternion.Angle(rest.rotation, native.rotation);
                var travel = Vector3.Distance(rest.position, native.position);
                maxAngle = Mathf.Max(maxAngle, angle);
                maxTravel = Mathf.Max(maxTravel, travel);
                return Mathf.Clamp01(Mathf.Max(angle / maxAngle, travel / maxTravel));
            }
        }

        // One B-36 turret slaved to the native turret weapon spawned on its hardpoint.
        class TurretDriver
        {
            readonly Transform rigFrame, hardpoint, marker, yawNode, gunsNode;
            readonly RigTurret data;
            readonly Vector3 up, right, forward, yawAxisParent, pitchAxisParent, yawPivot, pitchPivot, extendParent;
            readonly Vector3 yawRestPos, gunsRestPos;
            readonly Quaternion yawRestRot, gunsRestRot;
            Transform barrel;
            Quaternion lastBarrel;
            float az, el, ext, lastActive = -100f;

            public TurretDriver(Transform rigFrame, Transform hardpoint, Transform marker, Transform yaw, Transform guns, RigTurret data)
            {
                this.rigFrame = rigFrame;
                this.hardpoint = hardpoint;
                this.marker = marker;
                yawNode = yaw;
                gunsNode = guns;
                this.data = data;
                up = RigData.V(data.up);
                right = RigData.V(data.right);
                forward = RigData.V(data.forward);
                yawRestPos = yaw.localPosition;
                yawRestRot = yaw.localRotation;
                gunsRestPos = guns.localPosition;
                gunsRestRot = guns.localRotation;
                var toYawParent = yaw.parent.worldToLocalMatrix * rigFrame.localToWorldMatrix;
                yawPivot = toYawParent.MultiplyPoint3x4(RigData.V(data.pivot));
                yawAxisParent = toYawParent.MultiplyVector(RigData.V(data.yawAxis)).normalized * Mathf.Sign(Det(toYawParent));
                extendParent = toYawParent.MultiplyVector(up * data.retract);
                // The guns' parent is the yaw node, so its axis is taken at the rest pose.
                var toGunsParent = guns.parent.worldToLocalMatrix * rigFrame.localToWorldMatrix;
                pitchPivot = toGunsParent.MultiplyPoint3x4(RigData.V(data.gunsPivot));
                pitchAxisParent = toGunsParent.MultiplyVector(RigData.V(data.pitchAxis)).normalized * Mathf.Sign(Det(toGunsParent));
                ext = data.retract > 0f ? 0f : 1f;
            }

            Transform FindBarrel()
            {
                Transform best = null;
                var bestScore = -1;
                foreach (var t in hardpoint.GetComponentsInChildren<Transform>(true))
                {
                    if (t == hardpoint || t == marker || t.IsChildOf(marker)) continue;
                    var n = t.name.ToLowerInvariant();
                    var depth = 0;
                    for (var p = t; p != hardpoint; p = p.parent) depth++;
                    var score = depth + (n.Contains("muzzle") ? 300 : n.Contains("barrel") ? 200 : n.Contains("gun") ? 100 : 0);
                    if (score > bestScore) { bestScore = score; best = t; }
                }
                return best;
            }

            public void Update(float dt, bool hideNative)
            {
                if (barrel == null)
                {
                    barrel = FindBarrel();
                    if (barrel != null) lastBarrel = barrel.rotation;
                }
                if (hideNative)
                {
                    // The native turret does the shooting; only the B-36 turret is drawn.
                    foreach (var r in hardpoint.GetComponentsInChildren<Renderer>(true))
                        if (r.enabled && (r is MeshRenderer || r is SkinnedMeshRenderer)) r.enabled = false;
                }

                float wantAz = 0f, wantEl = 0f;
                if (barrel != null)
                {
                    if (Quaternion.Angle(lastBarrel, barrel.rotation) > 0.2f) lastActive = Time.time;
                    lastBarrel = barrel.rotation;
                    var d = rigFrame.InverseTransformDirection(barrel.forward);
                    float x = Vector3.Dot(d, right), y = Vector3.Dot(d, up), f = Vector3.Dot(d, forward);
                    wantAz = Mathf.Atan2(-x, f) * Mathf.Rad2Deg;
                    wantEl = Mathf.Atan2(y, Mathf.Sqrt(x * x + f * f)) * Mathf.Rad2Deg;
                }
                var active = Time.time - lastActive < TurretIdle;
                if (!active) { wantAz = 0f; wantEl = 0f; }
                var lim = data.limits;
                if (lim != null && lim.az != null && lim.az.Length == 2) wantAz = Mathf.Clamp(wantAz, lim.az[0], lim.az[1]);
                if (lim != null && lim.el != null && lim.el.Length == 2) wantEl = Mathf.Clamp(wantEl, lim.el[0], lim.el[1]);

                var target = data.retract > 0f ? (active ? 1f : 0f) : 1f;
                // Stow only once the guns are back at rest.
                if (target == 0f && (Mathf.Abs(az) > 1f || Mathf.Abs(el) > 1f)) target = 1f;
                ext = Mathf.MoveTowards(ext, target, dt / ExtendTime);
                if (ext > 0.999f || target == 0f)
                {
                    az = Mathf.MoveTowardsAngle(az, wantAz, TurretSlew * dt);
                    el = Mathf.MoveTowards(el, wantEl, TurretSlew * dt);
                }

                var qy = Quaternion.AngleAxis(az, yawAxisParent);
                yawNode.localRotation = qy * yawRestRot;
                yawNode.localPosition = yawPivot + qy * (yawRestPos - yawPivot) + extendParent * ext;
                // A stowed turret sits inside the fuselage; the source hides it there too.
                var show = ext > 0.01f;
                if (yawNode.gameObject.activeSelf != show) yawNode.gameObject.SetActive(show);
                var qp = Quaternion.AngleAxis(el, pitchAxisParent);
                gunsNode.localRotation = qp * gunsRestRot;
                gunsNode.localPosition = pitchPivot + qp * (gunsRestPos - pitchPivot);
            }
        }
    }
}
