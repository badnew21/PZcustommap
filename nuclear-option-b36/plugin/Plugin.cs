using System.Collections;
using System.IO;
using BepInEx;
using UnityEngine;

namespace B36Peacemaker
{
    // Companion plugin for the B-36 Peacemaker .nobp. The aircraft itself (flight, weapons,
    // turrets, bays) is all native game logic inside the Blueprinter bundle; this plugin only
    // animates the B-36 exterior so it shows what those hidden native parts are doing.
    // It uses no game types, so it keeps working across game updates that do not change
    // the prefab layout. Without it the B-36 still flies and fights, but its doors, gear,
    // turrets and propellers stay still.
    [BepInPlugin("badnew21.nuclearoption.b36peacemaker", "B-36 Peacemaker", "0.1.0")]
    public class Plugin : BaseUnityPlugin
    {
        RigData rig;

        void Awake()
        {
            using (var stream = typeof(Plugin).Assembly.GetManifestResourceStream("B36Peacemaker.b36_rig.json"))
            {
                if (stream == null)
                {
                    Logger.LogError("b36_rig.json is not embedded; rebuild the plugin.");
                    return;
                }
                using (var reader = new StreamReader(stream))
                    rig = JsonUtility.FromJson<RigData>(reader.ReadToEnd());
            }
            Logger.LogInfo("B-36 rig loaded: " + rig.parts.Length + " parts, " + rig.turrets.Length + " turrets.");
            StartCoroutine(Watch());
        }

        // Spawned aircraft are found by the rig frame the builder put under the prefab root.
        // Every aircraft has a rigidbody, so scanning those once a second is cheap.
        IEnumerator Watch()
        {
            var wait = new WaitForSeconds(1f);
            while (true)
            {
                foreach (var body in FindObjectsOfType<Rigidbody>())
                {
                    var frame = body.transform.Find(Markers.RigFrame);
                    if (frame == null && body.transform.parent == null) continue;
                    if (frame == null) frame = body.transform.root.Find(Markers.RigFrame);
                    if (frame == null || frame.GetComponent<B36Animator>() != null) continue;
                    var animator = frame.gameObject.AddComponent<B36Animator>();
                    animator.rig = rig;
                }
                yield return wait;
            }
        }
    }
}
