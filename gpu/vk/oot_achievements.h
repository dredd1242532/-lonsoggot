/* (longshot) The achievements (docs/achievements.md), shared by the game side (host/oot_achievements.c) and the menu
   (gpu/vk/vk_menu.cpp), so the two never disagree on the order or the names.

   OOT_ACHIEVEMENT(id, title, description, mode, item1, item2, item3): unlocked when Link holds all (OOT_ACH_ALL) or any
   (OOT_ACH_ANY) of the named pouch contents, which are gpu/vk/oot_cheat_items.h's names ("" for none). A bag tier is ANY of
   itself and the tiers above it, since the game may drop the smaller bag when it gives the bigger one.

   id is the key in the unlock file (user/achievements.txt): never change or reuse one. Titles and descriptions may change.
   Add new achievements at the end: the order is the menu's and rx_option_set's (OOT_ACHIEVEMENT_BASE + index). */
#ifndef OOT_ACHIEVEMENTS_H
#define OOT_ACHIEVEMENTS_H

#define OOT_ACH_ALL 0
#define OOT_ACH_ANY 1

#define OOT_ACHIEVEMENTS(X) \
    X("sword_and_shield", "Ready for Adventure", "Get the Kokiri Sword and the Deku Shield.", OOT_ACH_ALL, \
      "Weapon_Sword_Kokiri", "Weapon_Shield_Deku", "") \
    X("fairy_ocarina", "Fairy Ocarina", "Receive Saria's gift.", OOT_ACH_ALL, "Item_Ocarina_Fairy", "", "") \
    X("ocarina_of_time", "Ocarina of Time", "Receive the royal family's treasure.", OOT_ACH_ALL, "Item_Ocarina_Time", "", "") \
    X("stone_forest", "Kokiri's Emerald", "Free the Great Deku Tree from its curse.", OOT_ACH_ALL, "Item_Stone_Forest", "", "") \
    X("stone_fire", "Goron's Ruby", "Clear Dodongo's Cavern for the Gorons.", OOT_ACH_ALL, "Item_Stone_Fire", "", "") \
    X("stone_water", "Zora's Sapphire", "Rescue Princess Ruto from Lord Jabu-Jabu.", OOT_ACH_ALL, "Item_Stone_Water", "", "") \
    X("song_zelda", "Zelda's Lullaby", "Learn the royal family's song.", OOT_ACH_ALL, "Item_Melody_ZeldaLullaby", "", "") \
    X("song_epona", "Epona's Song", "Learn the song of Lon Lon Ranch.", OOT_ACH_ALL, "Item_Melody_EponaSong", "", "") \
    X("song_saria", "Saria's Song", "Learn Saria's song in the Sacred Forest Meadow.", OOT_ACH_ALL, "Item_Melody_SariaSong", "", "") \
    X("song_sun", "Sun's Song", "Learn the song that turns night to day.", OOT_ACH_ALL, "Item_Melody_SunSong", "", "") \
    X("song_time", "Song of Time", "Learn the song that moves the Blocks of Time.", OOT_ACH_ALL, "Item_Melody_TimeSong", "", "") \
    X("song_storms", "Song of Storms", "Learn the song from the windmill.", OOT_ACH_ALL, "Item_Melody_StormSong", "", "") \
    X("song_minuet", "Minuet of Forest", "Learn the warp song to the Sacred Forest Meadow.", OOT_ACH_ALL, "Item_Melody_ForestMinuet", "", "") \
    X("song_bolero", "Bolero of Fire", "Learn the warp song to Death Mountain Crater.", OOT_ACH_ALL, "Item_Melody_FireBolero", "", "") \
    X("song_serenade", "Serenade of Water", "Learn the warp song to Lake Hylia.", OOT_ACH_ALL, "Item_Melody_WaterSerenade", "", "") \
    X("song_nocturne", "Nocturne of Shadow", "Learn the warp song to the Graveyard.", OOT_ACH_ALL, "Item_Melody_ShadowNocturne", "", "") \
    X("song_requiem", "Requiem of Spirit", "Learn the warp song to the Desert Colossus.", OOT_ACH_ALL, "Item_Melody_SpiritRequiem", "", "") \
    X("song_prelude", "Prelude of Light", "Learn the warp song to the Temple of Time.", OOT_ACH_ALL, "Item_Melody_LightPrelude", "", "") \
    X("temple_forest", "Forest Temple", "Complete the Forest Temple and receive the Forest Medallion.", OOT_ACH_ALL, "Item_Medal_Forest", "", "") \
    X("temple_fire", "Fire Temple", "Complete the Fire Temple and receive the Fire Medallion.", OOT_ACH_ALL, "Item_Medal_Fire", "", "") \
    X("temple_water", "Water Temple", "Complete the Water Temple and receive the Water Medallion.", OOT_ACH_ALL, "Item_Medal_Water", "", "") \
    X("temple_shadow", "Shadow Temple", "Complete the Shadow Temple and receive the Shadow Medallion.", OOT_ACH_ALL, "Item_Medal_Shadow", "", "") \
    X("temple_spirit", "Spirit Temple", "Complete the Spirit Temple and receive the Spirit Medallion.", OOT_ACH_ALL, "Item_Medal_Spirit", "", "") \
    X("bomb_bag_big", "Big Bomb Bag", "Upgrade to the Big Bomb Bag.", OOT_ACH_ANY, "Item_BombBag_Medium", "Item_BombBag_Large", "") \
    X("bomb_bag_biggest", "Biggest Bomb Bag", "Upgrade to the Biggest Bomb Bag.", OOT_ACH_ANY, "Item_BombBag_Large", "", "") \
    X("quiver_big", "Big Quiver", "Upgrade to the Big Quiver.", OOT_ACH_ANY, "Item_Quiver_Medium", "Item_Quiver_Large", "") \
    X("quiver_biggest", "Biggest Quiver", "Upgrade to the Biggest Quiver.", OOT_ACH_ANY, "Item_Quiver_Large", "", "") \
    X("bullet_bag_big", "Big Bullet Bag", "Upgrade to the Big Bullet Bag.", OOT_ACH_ANY, "Item_BulletBag_Medium", "Item_BulletBag_Large", "") \
    X("bullet_bag_biggest", "Biggest Bullet Bag", "Upgrade to the Biggest Bullet Bag.", OOT_ACH_ANY, "Item_BulletBag_Large", "", "")

/* WIP, not in the list yet: "hero_of_time", "Hero of Time", "Defeat Ganon." No signal for it is known: no item marks it, so it
   needs a game flag or the ending's event name, found in a run (docs/achievements.md, "Open questions"). It joins the end of
   the list once found, with a condition of its own kind. */

/* rx_option_set ids from the game side: OOT_ACHIEVEMENT_BASE + index is 1 once that achievement is unlocked (the unlock file's,
   sent once at the start, then each new one); OOT_ACHIEVEMENT_TOAST_ID with an index asks for its pop-up. Past the Inventory
   page's OOT_CHEAT_ITEM_BASE + its items. */
#define OOT_ACHIEVEMENT_TOAST_ID 8191
#define OOT_ACHIEVEMENT_BASE 8192
/* rx_option_set(OOT_ACHIEVEMENTS_PAUSED_ID, 1) from the game side once a debug tool has been used in this run (host/oot_achievements.c
   hos_oot_achievements_cheated): none unlock until the game starts again, and the Achievements page says why. The game side
   decides this itself, from the cheat actions it runs: no option of vk_api.h, which stamps the shader caches. */
#define OOT_ACHIEVEMENTS_PAUSED_ID 8190

#endif
