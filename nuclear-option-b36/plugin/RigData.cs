using System;
using UnityEngine;

namespace B36Peacemaker
{
    // Mirrors b36_rig.json, written by tools/convert_b36.py. Unity model space:
    // +X right wing, +Y up, +Z toward the nose, metres, degrees, origin at mid-length.
    [Serializable]
    public class RigData
    {
        public string source;
        public float length;
        public float span;
        public float height;
        public float[] cg;
        public RigPart[] parts;
        public RigTurret[] turrets;
        public RigLeg[] legs;
        public RigBay[] bays;

        public static Vector3 V(float[] a)
        {
            return new Vector3(a[0], a[1], a[2]);
        }
    }

    [Serializable]
    public class RigPart
    {
        public string name;
        public string kind;   // aileron, elevator, rudder, flap, bombdoor, gear, prop, wheel
        public string side;   // L, R, C; for wheels the leg name
        public int bay;
        public int engine;
        public float radius;
        public float[] pivot;
        public float[] axis;  // rotation axis; a positive angle turns by the right-hand rule
        public float angle;   // full deflection, open angle or stowed angle
    }

    [Serializable]
    public class RigTurret
    {
        public string name;
        public string guns;
        public string kind;   // upper, lower, nose, tail
        public float[] pivot;
        public float[] gunsPivot;
        public float retract; // metres the turret rises along "up" when extended; 0 = fixed
        public float[] yawAxis;
        public float[] pitchAxis;
        public float[] up;
        public float[] right;
        public float[] forward;
        public RigLimits limits;
    }

    [Serializable]
    public class RigLimits
    {
        public float[] az;    // null = unlimited
        public float[] el;
    }

    [Serializable]
    public class RigLeg
    {
        public string leg;
        public string[] wheels;
        public float radius;
        public float[] center;
        public float[] contact;
    }

    [Serializable]
    public class RigBay
    {
        public int index;
        public float zFwd;
        public float zAft;
        public string[] doors;
        public float topY;
        public float bottomY;
        public float halfWidth;
        public float hardpointY;
    }
}
